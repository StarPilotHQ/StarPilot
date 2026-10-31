#!/usr/bin/env python3
import argparse
import hashlib
import io
import json
import multiprocessing
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ProcessPoolExecutor, as_completed
from collections import Counter, namedtuple
from dataclasses import dataclass
from pathlib import Path

import tree_sitter_c as ts_c
import tree_sitter as ts


ROOT = Path(__file__).resolve().parents[3]
SAFETY_DIR = ROOT / "opendbc" / "safety"
BLUEPILOT_SAFETY_DIR = ROOT / "opendbc" / "bluepilot_lateral" / "safety"
SAFETY_SOURCE_DIRS = (SAFETY_DIR, BLUEPILOT_SAFETY_DIR)
SAFETY_TESTS_DIR = ROOT / "opendbc" / "safety" / "tests"
SAFETY_C_REL = Path("opendbc/safety/tests/libsafety/safety.c")

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"

COMPARISON_OPERATOR_MAP = {
  "==": "!=",
  "!=": "==",
  ">": "<=",
  ">=": "<",
  "<": ">=",
  "<=": ">",
}

MUTATOR_FAMILIES = {
  "increment": ("update_expression", {"++": "--"}),
  "decrement": ("update_expression", {"--": "++"}),
  "comparison": ("binary_expression", COMPARISON_OPERATOR_MAP),
  "boundary": ("number_literal", {}),
  "bitwise_assignment": ("assignment_expression", {"&=": "|=", "|=": "&=", "^=": "&="}),
  "bitwise": ("binary_expression", {"&": "|", "|": "&", "^": "&"}),
  "arithmetic_assignment": ("assignment_expression", {"+=": "-=", "-=": "+=", "*=": "/=", "/=": "*=", "%=": "*="}),
  "arithmetic": ("binary_expression", {"+": "-", "-": "+", "*": "/", "/": "*", "%": "*"}),
  "remove_negation": ("unary_expression", {"!": ""}),
}


_RawSite = namedtuple('_RawSite', 'expr_start expr_end op_start op_end line original_op mutated_op mutator')


@dataclass(frozen=True)
class MutationSite:
  site_id: int
  expr_start: int
  expr_end: int
  op_start: int
  op_end: int
  line: int
  original_op: str
  mutated_op: str
  mutator: str
  origin_file: Path
  origin_line: int


@dataclass(frozen=True)
class MutantResult:
  site: MutationSite
  outcome: str  # killed | survived | infra_error
  test_sec: float
  details: str
  failure_kind: str | None = None
  exit_code: int | None = None
  stderr_tail: str = ""
  stdout_tail: str = ""
  unittest_output_tail: str = ""


@dataclass(frozen=True)
class TestRun:
  ran: int
  skipped: int
  suppressed: int
  failures: tuple[str, ...]
  errors: tuple[str, ...]
  output_tail: str = ""


class IsolatedRunError(RuntimeError):
  def __init__(self, kind: str, message: str, *, exit_code: int | None, stderr: str, stdout: str):
    super().__init__(message)
    self.kind = kind
    self.exit_code = exit_code
    self.stderr_tail = stderr[-32768:]
    self.stdout_tail = stdout[-8192:]


def colorize(text, color):
  term = os.environ.get("TERM", "")
  if not sys.stdout.isatty() or term in ("", "dumb") or "NO_COLOR" in os.environ:
    return text
  return f"{color}{text}{ANSI_RESET}"


def format_mutation(original_op, mutated_op):
  return colorize(f"{original_op}->{mutated_op}", ANSI_RED)


def _parse_int_literal(token):
  m = re.fullmatch(r"([0-9][0-9a-fA-FxX]*)([uUlL]*)", token)
  if m is None:
    return None
  body, suffix = m.groups()
  try:
    value = int(body, 0)
  except ValueError:
    return None
  base = "hex" if body.lower().startswith("0x") else "dec"
  return value, base, suffix


def _site_key(site):
  return (site.op_start, site.op_end, site.mutator)


def _is_in_constexpr_context(node):
  """Find contexts that cannot contain the runtime mutant selector."""
  current = node.parent
  while current is not None:
    if current.type == "call_expression":
      function = current.child_by_field_name("function")
      if function is not None and function.text == b"_Static_assert":
        return True
    if current.type == "init_declarator":
      decl = current.parent
      if decl and decl.type == "declaration":
        for child in decl.children:
          if child.type == "storage_class_specifier" and child.text == b"static":
            return True
        if decl.parent and decl.parent.type == "translation_unit":
          return True
    current = current.parent
  return False


def _prepare_for_parsing(txt):
  """Blank line markers and replace __typeof__() for tree-sitter. Preserves byte offsets."""
  result = re.sub(
    r'^[ \t]*#[ \t]+\d+[ \t]+"[^\n]*',
    lambda m: " " * len(m.group()),
    txt,
    flags=re.MULTILINE,
  )
  # Replace __typeof__(...) with padded int (handle nested parens)
  parts = []
  i = 0
  for m in re.finditer(r"(?:__typeof__|typeof)\s*\(", result):
    if m.start() < i:
      continue  # skip nested typeof inside already-replaced region
    parts.append(result[i:m.start()])
    depth = 1
    j = m.end()
    while j < len(result) and depth > 0:
      if result[j] == "(":
        depth += 1
      elif result[j] == ")":
        depth -= 1
      j += 1
    parts.append("int" + " " * (j - m.start() - 3))
    i = j
  parts.append(result[i:])
  return "".join(parts)


def enumerate_sites(input_source, preprocessed_file):
  subprocess.run([
    "cc", "-E", "-std=gnu11", "-nostdlib", "-fno-builtin", "-DALLOW_DEBUG",
    f"-I{ROOT}", f"-I{ROOT / 'opendbc/safety/board'}",
    str(input_source), "-o", str(preprocessed_file),
  ], cwd=ROOT, capture_output=True, check=True)

  txt = preprocessed_file.read_text()

  # Build line map from preprocessor directives
  line_map = {}
  current_map_file = None
  current_map_line = None
  directive_re = re.compile(r'^\s*#\s*(\d+)\s+"([^"]+)"')
  for pp_line_num, pp_line in enumerate(txt.splitlines(keepends=True), start=1):
    m = directive_re.match(pp_line)
    if m:
      current_map_line = int(m.group(1))
      current_map_file = Path(m.group(2)).resolve()
      continue
    if current_map_file is not None and current_map_line is not None:
      line_map[pp_line_num] = (current_map_file, current_map_line)
      current_map_line += 1

  # Parse with tree-sitter
  parser = ts.Parser(ts.Language(ts_c.language()))
  tree = parser.parse(_prepare_for_parsing(txt).encode())

  # Build rule map
  rule_map = {}
  counts = {}
  for mutator, (node_kind, op_map) in MUTATOR_FAMILIES.items():
    counts[mutator] = 0
    if mutator == "boundary":
      continue
    for original_op, mutated_op in op_map.items():
      rule_map.setdefault((node_kind, original_op), []).append((mutator, original_op, mutated_op))

  # Walk tree to find mutation sites
  deduped = {}
  build_incompatible_keys = set()
  stack = [tree.root_node]
  while stack:
    node = stack.pop()
    kind = node.type

    # Boundary mutations: find number_literals inside comparison operands
    if kind == "binary_expression":
      cmp_op = node.child_by_field_name("operator")
      if cmp_op and cmp_op.type in COMPARISON_OPERATOR_MAP:
        lit_stack = []
        for field in ("left", "right"):
          operand = node.child_by_field_name(field)
          if operand:
            lit_stack.append(operand)
        while lit_stack:
          n = lit_stack.pop()
          if n.type == "number_literal":
            token = txt[n.start_byte:n.end_byte]
            parsed = _parse_int_literal(token)
            if parsed:
              value, base, suffix = parsed
              mutated = f"0x{value + 1:X}{suffix}" if base == "hex" else f"{value + 1}{suffix}"
              line = n.start_point[0] + 1
              bsite = _RawSite(n.start_byte, n.end_byte, n.start_byte, n.end_byte, line, token, mutated, "boundary")
              key = _site_key(bsite)
              deduped[key] = bsite
              if _is_in_constexpr_context(n):
                build_incompatible_keys.add(key)
          lit_stack.extend(n.children)

    # Operator mutations: any node with an operator child
    op_child = node.child_by_field_name("operator")
    if op_child:
      for mutator, original_op, mutated_op in rule_map.get((kind, op_child.type), []):
        line = node.start_point[0] + 1
        site = _RawSite(node.start_byte, node.end_byte, op_child.start_byte, op_child.end_byte, line, original_op, mutated_op, mutator)
        key = _site_key(site)
        deduped[key] = site
        if _is_in_constexpr_context(node):
          build_incompatible_keys.add(key)

    stack.extend(node.children)

  sites = sorted(deduped.values(), key=lambda s: (s.op_start, s.mutator))
  out = []
  build_incompatible_site_ids = set()
  for s in sites:
    mapped = line_map.get(s.line)
    if mapped is None:
      continue
    origin_file, origin_line = mapped
    if not any(origin_file.is_relative_to(source_dir) for source_dir in SAFETY_SOURCE_DIRS):
      continue
    site_id = len(out)
    site = MutationSite(
      site_id=site_id, expr_start=s.expr_start, expr_end=s.expr_end,
      op_start=s.op_start, op_end=s.op_end, line=s.line,
      original_op=s.original_op, mutated_op=s.mutated_op, mutator=s.mutator,
      origin_file=origin_file, origin_line=origin_line,
    )
    if _site_key(s) in build_incompatible_keys:
      build_incompatible_site_ids.add(site_id)
    out.append(site)
    counts[s.mutator] += 1
  return out, counts, build_incompatible_site_ids, txt


def equivalent_division_candidates(source, sites, excluded_ids):
  """Recognize only closed unsigned-int division used directly in a comparison.

  Replacing one literal may preserve the entire quotient. An unchanged quotient
  AND C type preserves the comparison for every value of its other operand.
  This deliberately does not reason about variable expressions or conversions.
  """
  parser = ts.Parser(ts.Language(ts_c.language()))
  tree = parser.parse(_prepare_for_parsing(source).encode())
  literals = {}
  stack = [tree.root_node]
  while stack:
    node = stack.pop()
    if node.type == "number_literal":
      literals[node.start_byte, node.end_byte] = node
    stack.extend(node.children)

  def unwrap(node):
    while node is not None and node.type == "parenthesized_expression" and len(node.named_children) == 1:
      node = node.named_children[0]
    return node

  def outer(node):
    while node.parent is not None and node.parent.type == "parenthesized_expression":
      node = node.parent
    return node

  def unsigned_literal(node):
    node = unwrap(node)
    if node is None or node.type != "number_literal":
      return None
    token = source[node.start_byte:node.end_byte]
    # Only the U suffix; decimal/hex, with no signed/octal/long/type ambiguity.
    if re.fullmatch(r"(?:0|[1-9][0-9]*|0[xX][0-9a-fA-F]+)[uU]", token) is None:
      return None
    parsed = _parse_int_literal(token)
    return (node, parsed[0]) if parsed is not None and parsed[0] <= 0xFFFFFFFF else None

  records = []
  for site in sites:
    if site.mutator != "boundary" or site.site_id in excluded_ids:
      continue
    literal = literals.get((site.op_start, site.op_end))
    if literal is None:
      continue
    division = outer(literal).parent
    if division is None or division.type != "binary_expression":
      continue
    operator = division.child_by_field_name("operator")
    if operator is None or operator.type != "/":
      continue
    left, right = (unsigned_literal(division.child_by_field_name(field)) for field in ("left", "right"))
    comparison = outer(division).parent
    if left is None or right is None or comparison is None or comparison.type != "binary_expression":
      continue
    comparison_op = comparison.child_by_field_name("operator")
    if comparison_op is None or comparison_op.type not in COMPARISON_OPERATOR_MAP:
      continue
    mutated = unsigned_literal(literal)
    parsed_mutated = _parse_int_literal(site.mutated_op)
    if mutated is None or parsed_mutated is None or parsed_mutated[2].lower() != "u" or parsed_mutated[0] > 0xFFFFFFFF:
      continue
    numerator, denominator = left[1], right[1]
    next_numerator = parsed_mutated[0] if left[0] == literal else numerator
    next_denominator = parsed_mutated[0] if right[0] == literal else denominator
    if denominator == 0 or next_denominator == 0 or numerator // denominator != next_numerator // next_denominator:
      continue
    original = source[division.start_byte:division.end_byte]
    offset = site.op_start - division.start_byte
    if original[offset:offset + len(site.original_op)] != site.original_op:
      raise RuntimeError("equivalence literal no longer matches preprocessed source")
    changed = original[:offset] + site.mutated_op + original[offset + len(site.original_op):]
    records.append({"site_id": site.site_id, "source": str(site.origin_file.relative_to(ROOT)),
                    "line": site.origin_line, "mutator": site.mutator, "original_op": site.original_op,
                    "mutated_op": site.mutated_op, "op_start": site.op_start, "op_end": site.op_end,
                    "expression_start": division.start_byte, "expression_end": division.end_byte,
                    "comparison_start": comparison.start_byte, "comparison_end": comparison.end_byte,
                    "comparison_operator": comparison_op.type, "original_expression": original,
                    "mutated_expression": changed, "c_type": "unsigned int", "value": numerator // denominator,
                    "proof_kind": "closed-unsigned-division-v1"})
  return records


def division_proof_source(records):
  lines = ["/* Compiler proof of equal closed unsigned-int division expressions. */"]
  for record in records:
    original, changed = record["original_expression"], record["mutated_expression"]
    for expression in (original, changed):
      lines.append(f'_Static_assert(__builtin_types_compatible_p(__typeof__({expression}), unsigned int), "unsigned int type");')
      lines.append(f'_Static_assert(({expression}) == {record["value"]}U, "quotient value");')
    lines.append(f'_Static_assert(({original}) == ({changed}), "equivalent quotient");')
  return "\n".join(lines) + "\n"


def prove_equivalent_divisions(source, sites, excluded_ids):
  records = equivalent_division_candidates(source, sites, excluded_ids)
  if not records:
    return []
  compiler = Path(shutil.which("cc") or "").resolve()
  if not compiler.is_file():
    raise RuntimeError("equivalence proof requires the actual C compiler")
  proof = division_proof_source(records)
  flags = ["-fPIC", "-g0", "-O0", "-DALLOW_DEBUG", "-Werror", "-std=gnu11", "-fno-builtin", "-fsyntax-only", "-x", "c", "-"]
  result = subprocess.run([str(compiler), *flags], input=proof, cwd=ROOT, capture_output=True, text=True, timeout=30)
  if result.returncode:
    raise RuntimeError(f"equivalence compiler proof failed: {result.stderr[-4096:]}")
  version = subprocess.run([str(compiler), "--version"], cwd=ROOT, capture_output=True, text=True, timeout=30)
  target = subprocess.run([str(compiler), "-dumpmachine"], cwd=ROOT, capture_output=True, text=True, timeout=30)
  if version.returncode or target.returncode or not version.stdout.strip() or not re.fullmatch(r"[a-zA-Z0-9_.+-]+", target.stdout.strip()):
    raise RuntimeError("equivalence compiler identity is unavailable")
  receipt = {"compiler_argv": [str(compiler), *flags], "compiler_version": version.stdout.strip(),
             "compiler_target": target.stdout.strip(), "compiler_sha256": hashlib.sha256(compiler.read_bytes()).hexdigest(),
             "compiler_flags": flags, "proof_source_sha256": hashlib.sha256(proof.encode()).hexdigest()}
  return [{**record, **receipt} for record in records]


def verify_equivalence_artifact(path):
  """Reconstruct proofs from pinned real inputs, rather than trusting the claims."""
  raw = path.read_bytes()
  artifact = json.loads(raw)
  with tempfile.TemporaryDirectory(prefix="mutation-equivalence-verify-") as directory:
    sites, _, excluded, source = enumerate_sites(ROOT / SAFETY_C_REL, Path(directory) / "source.i")
    if artifact["discovered"] != len(sites) or artifact["preprocessed_source_sha256"] != hashlib.sha256(source.encode()).hexdigest():
      raise RuntimeError("equivalence discovery/source identity changed")
    if {item["site_id"] for item in artifact["pruned_build_incompatible"]} != excluded:
      raise RuntimeError("constant-context partition changed")
    proofs = prove_equivalent_divisions(source, sites, excluded)
    if artifact["proven_equivalent"] != proofs:
      raise RuntimeError("equivalence partition or compiler proof changed")
  return hashlib.sha256(raw).hexdigest()


def _build_core_tests(catalog):
  """Build test ordering for core (non-mode) files.

  Tests are ordered by how widely each method is shared. Methods inherited by
  many classes exercise the most fundamental safety logic and run first.

  Keep every implementation of each method.
  """
  method_freq = {}
  method_by_module = {}
  for name in sorted(catalog.keys()):
    for test_id in catalog[name]:
      method = test_id.rsplit(".", 1)[-1]
      method_freq[method] = method_freq.get(method, 0) + 1
      if method not in method_by_module:
        method_by_module[method] = {}
      method_by_module[method].setdefault(name, []).append(test_id)
  # Round-robin: first instance of each method (by freq), then second, etc.
  # This ensures diverse early coverage with failfast.
  sorted_methods = sorted(method_freq, key=lambda m: -method_freq[m])
  ordered = []
  max_implementations = max(sum(len(ids) for ids in module_map.values()) for module_map in method_by_module.values())
  for round_idx in range(max_implementations):
    for m in sorted_methods:
      ids = [test_id for module in sorted(method_by_module[m]) for test_id in method_by_module[m][module]]
      if round_idx < len(ids):
        ordered.append(ids[round_idx])
  return ordered


def build_priority_tests(site, catalog, core_tests):
  """Build an ordered list of test IDs for a mutation site.

  For mode files: all tests for the mode's vehicle family, including newer variant suites.
  For core files: uses the complete pre-computed core_tests ordering.
  """
  src = site.origin_file
  rel_parts = src.relative_to(ROOT).parts
  is_mode = (len(rel_parts) >= 4 and rel_parts[:3] == ("opendbc", "safety", "modes")) or src.parent == BLUEPILOT_SAFETY_DIR

  if is_mode:
    family = src.stem.split("_", 1)[0]
    family_modules = [name for name in sorted(catalog) if name == f"test_{family}.py" or name.startswith(f"test_{family}_") or
                      (family == "ford" and name == "test_angle_native.py")]
    if family_modules:
      return [test_id for name in family_modules for test_id in catalog[name]]
    # Unmapped mode files still exercise the full safety suite rather than receive zero targets.
    return [test_id for name in sorted(catalog) for test_id in catalog[name]]
  return core_tests


def format_site_snippet(site, context_lines=2):
  source = site.origin_file
  text = source.read_text()
  lines = text.splitlines()
  display_ln = site.origin_line
  line_idx = display_ln - 1
  start = max(0, line_idx - context_lines)
  end = min(len(lines), line_idx + context_lines + 1)

  line_text = lines[line_idx]
  rel_start = line_text.find(site.original_op)
  if rel_start < 0:
    rel_start = 0
  rel_end = rel_start + len(site.original_op)

  snippet_lines = []
  width = len(str(end))
  for idx in range(start, end):
    num = idx + 1
    prefix = ">" if idx == line_idx else " "
    line = lines[idx]
    if idx == line_idx:
      marker = colorize(f"[[{site.original_op}->{site.mutated_op}]]", ANSI_RED)
      line = f"{line[:rel_start]}{marker}{line[rel_end:]}"
    snippet_lines.append(f"{prefix} {num:>{width}} | {line}")
  return "\n".join(snippet_lines)


def render_progress(completed, total, killed, survived, infra, elapsed_sec):
  bar_width = 30
  filled = int((completed / total) * bar_width)
  bar = "#" * filled + "-" * (bar_width - filled)

  rate = completed / elapsed_sec if elapsed_sec > 0 else 0.0
  remaining = total - completed
  eta = (remaining / rate) if rate > 0 else 0.0

  killed_text = colorize(f"k:{killed}", ANSI_GREEN)
  survived_text = colorize(f"s:{survived}", ANSI_RED)
  infra_text = colorize(f"i:{infra}", ANSI_YELLOW)

  return f"[{bar}] {completed}/{total} {killed_text} {survived_text} {infra_text} mps:{rate:.2f} elapsed:{elapsed_sec:.1f}s eta:{eta:.1f}s"


def print_live_status(text, *, final=False):
  if sys.stdout.isatty():
    print("\r" + text, end="\n" if final else "", flush=True)
  else:
    print(text, flush=True)


def _discover_test_catalog():
  loader = unittest.TestLoader()
  catalog = {}
  test_files = [*sorted(SAFETY_TESTS_DIR.glob("test_*.py")), *sorted((BLUEPILOT_SAFETY_DIR / "tests").glob("test_*.py"))]
  for test_file in test_files:
    module_name = ".".join(test_file.relative_to(ROOT).with_suffix("").parts)
    suite = loader.loadTestsFromName(module_name)
    catalog[test_file.name] = [t.id() for group in suite for t in group]
  return catalog


def run_unittest(targets, lib_path, mutant_id, verbose):
  from opendbc.safety.tests.libsafety import libsafety_py
  libsafety_py.load(lib_path)
  libsafety_py.libsafety.mutation_set_active_mutant(mutant_id)

  if verbose:
    print("Running unittest targets:", ", ".join(targets), flush=True)

  loader = unittest.TestLoader()
  stream = io.StringIO()
  runner = unittest.TextTestRunner(stream=stream, verbosity=0, failfast=True)

  suite = unittest.TestSuite()
  for target in targets:
    suite.addTests(loader.loadTestsFromName(target))
  result = runner.run(suite)
  suppressed_indices = set()
  method_skips = 0
  for test, _reason in result.skipped:
    skipped_id = test.id()
    class_match = re.fullmatch(r"setUpClass \(([^)]+)\)", skipped_id)
    module_match = re.fullmatch(r"setUpModule \(([^)]+)\)", skipped_id)
    if class_match or module_match:
      prefix = (class_match or module_match).group(1) + "."
      matched = {index for index, target in enumerate(targets) if target.startswith(prefix)}
      if not matched:
        raise RuntimeError(f"unmatched unittest skip holder: {skipped_id}")
      suppressed_indices.update(matched)
    elif skipped_id in targets:
      method_skips += 1
    else:
      raise RuntimeError(f"unexpected unittest skip holder: {skipped_id}")
  return TestRun(result.testsRun, method_skips, len(suppressed_indices),
                 tuple(test.id() for test, _ in result.failures),
                 tuple(test.id() for test, _ in result.errors), stream.getvalue()[-32768:])


def _instrument_source(source, sites):
  # Sort by start ascending, end descending (outermost first when same start)
  sorted_sites = sorted(sites, key=lambda s: (s.expr_start, -s.expr_end))

  # Build containment forest using a stack
  roots = []
  stack = []
  for site in sorted_sites:
    while stack and stack[-1][0].expr_end <= site.expr_start:
      stack.pop()
    node = [site, []]
    if stack:
      stack[-1][1].append(node)
    else:
      roots.append(node)
    stack.append(node)

  def build_replacement(site, children):
    parts = []
    pos = site.expr_start
    op_rel = None
    running_len = 0

    for child_site, child_children in children:
      seg = source[pos : child_site.expr_start]
      if op_rel is None and site.op_start >= pos and site.op_start < child_site.expr_start:
        op_rel = running_len + (site.op_start - pos)
      parts.append(seg)
      running_len += len(seg)

      child_repl = build_replacement(child_site, child_children)
      parts.append(child_repl)
      running_len += len(child_repl)
      pos = child_site.expr_end

    seg = source[pos : site.expr_end]
    if op_rel is None and site.op_start >= pos:
      op_rel = running_len + (site.op_start - pos)
    parts.append(seg)

    expr_text = "".join(parts)
    op_len = site.op_end - site.op_start
    assert op_rel is not None and expr_text[op_rel : op_rel + op_len] == site.original_op, (
      f"Operator mismatch (site_id={site.site_id}): expected {site.original_op!r} at offset {op_rel}"
    )
    mutated_expr = f"{expr_text[:op_rel]}{site.mutated_op}{expr_text[op_rel + op_len :]}"
    return f"((__mutation_active_id == {site.site_id}) ? ({mutated_expr}) : ({expr_text}))"

  result_parts = []
  pos = 0
  for site, children in roots:
    result_parts.append(source[pos : site.expr_start])
    result_parts.append(build_replacement(site, children))
    pos = site.expr_end
  result_parts.append(source[pos:])
  return "".join(result_parts)


def compile_mutated_library(preprocessed_source, sites, output_so):
  instrumented = _instrument_source(preprocessed_source, sites)

  prelude = """
    static int __mutation_active_id = -1;
    void mutation_set_active_mutant(int id) { __mutation_active_id = id; }
  """
  marker_re = re.compile(r'^\s*#\s+\d+\s+"[^\n]*\n?', re.MULTILINE)
  instrumented = prelude + marker_re.sub("", instrumented)

  mutation_source = output_so.with_suffix(".c")
  mutation_source.write_text(instrumented)

  subprocess.run([
    "cc", "-shared", "-fPIC", "-w", "-fno-builtin", "-std=gnu11",
    "-g0", "-O0", "-DALLOW_DEBUG",
    str(mutation_source), "-o", str(output_so),
  ], cwd=ROOT, check=True)


def _run_isolated_unittest(site_id, targets, lib_path, timeout_sec):
  script = """import faulthandler, json, sys
from pathlib import Path
from dataclasses import asdict
from opendbc.safety.tests import mutation
faulthandler.enable(all_threads=True)
request = json.loads(sys.stdin.read())
result = mutation.run_unittest(request['targets'], Path(request['library']), request['site_id'], False)
print(json.dumps(asdict(result)), flush=True)
"""
  request = json.dumps({"targets": targets, "library": str(lib_path), "site_id": site_id})
  child_env = dict(os.environ, PWD=str(ROOT))
  proc = subprocess.Popen([sys.executable, "-c", script], cwd=ROOT, stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True, env=child_env)
  try:
    stdout, stderr = proc.communicate(request, timeout=timeout_sec)
  except subprocess.TimeoutExpired as exc:
    os.killpg(proc.pid, signal.SIGKILL)
    stdout, stderr = proc.communicate()
    raise IsolatedRunError("timeout", f"isolated mutant timed out after {timeout_sec:.1f}s",
                           exit_code=proc.returncode, stderr=stderr, stdout=stdout) from exc
  if proc.returncode != 0:
    raise IsolatedRunError("signal" if proc.returncode < 0 else "child_exit",
                           f"isolated mutant exit={proc.returncode}", exit_code=proc.returncode,
                           stderr=stderr, stdout=stdout)
  try:
    data = json.loads(stdout.splitlines()[-1])
    return TestRun(**data)
  except (IndexError, ValueError, TypeError) as exc:
    raise IsolatedRunError("missing_result", "isolated mutant missing result", exit_code=proc.returncode,
                           stderr=stderr, stdout=stdout) from exc


def eval_mutant(site, targets, lib_path, verbose, timeout_sec):
  try:
    t0 = time.perf_counter()
    run = _run_isolated_unittest(site.site_id, targets, lib_path, timeout_sec)
    duration = time.perf_counter() - t0
    if run.errors or ((run.ran + run.suppressed != len(targets)) and not run.failures) or run.ran == run.skipped:
      return MutantResult(site, "infra_error", duration, f"unittest errors={run.errors} ran={run.ran} skipped={run.skipped} suppressed={run.suppressed}",
                          failure_kind="unittest_error", unittest_output_tail=run.output_tail)
    if run.failures:
      return MutantResult(site, "killed", duration, run.failures[0], unittest_output_tail=run.output_tail)
    return MutantResult(site, "survived", duration, "")
  except IsolatedRunError as exc:
    return MutantResult(site, "infra_error", 0.0, str(exc), failure_kind=exc.kind,
                        exit_code=exc.exit_code, stderr_tail=exc.stderr_tail, stdout_tail=exc.stdout_tail)
  except Exception as exc:
    return MutantResult(site, "infra_error", 0.0, str(exc), failure_kind="worker_exception")


def completed_mutant_result(future, site):
  """A failed worker is infrastructure loss, not proof that a mutant was killed."""
  try:
    return future.result()
  except Exception as exc:
    return MutantResult(site, "infra_error", 0.0, f"{type(exc).__name__}: {exc}", failure_kind="pool_exception")


def require_full_baseline(catalog, sites, lib_path, verbose):
  """Validate every discovered test before any mutant is submitted to workers."""
  if not catalog or any(not ids for ids in catalog.values()):
    raise RuntimeError("empty safety test catalog or module")
  baseline_ids = [test_id for module in sorted(catalog) for test_id in catalog[module]]
  baseline = run_unittest(baseline_ids, lib_path, mutant_id=-1, verbose=verbose)
  if (baseline.failures or baseline.errors or baseline.ran + baseline.suppressed != len(baseline_ids) or
      baseline.ran == baseline.skipped):
    raise RuntimeError(f"unmutated full safety baseline failed: {baseline}")

  core_tests = _build_core_tests(catalog)
  targets = {site.site_id: build_priority_tests(site, catalog, core_tests) for site in sites}
  missing = [site_id for site_id, ids in targets.items() if not ids]
  if missing:
    raise RuntimeError(f"mutants lack test targets: {missing[:20]} ({len(missing)} total)")
  return targets


def write_results_json(path: Path, *, discovered_sites, pruned_ids, results, site_targets,
                       preprocessed_source: str, baseline_sec: float, proven_equivalent=()) -> None:
  """Retain complete bounded diagnostics without changing mutation verdicts."""
  equivalent_ids = {item["site_id"] for item in proven_equivalent}
  if (len(equivalent_ids) != len(proven_equivalent) or equivalent_ids & set(pruned_ids) or
      not equivalent_ids <= {site.site_id for site in discovered_sites}):
    raise RuntimeError("equivalence partition must contain unique, independently pruned sites")
  expected_ids = {site.site_id for site in discovered_sites} - set(pruned_ids) - equivalent_ids
  result_ids = [result.site.site_id for result in results]
  if len(result_ids) != len(set(result_ids)) or set(result_ids) != expected_ids:
    raise RuntimeError("mutation result artifact requires one outcome for every executed site")
  target_sets: list[list[str]] = []
  target_indexes: dict[tuple[str, ...], int] = {}
  records = []
  for result in sorted(results, key=lambda item: item.site.site_id):
    targets = tuple(site_targets[result.site.site_id])
    if targets not in target_indexes:
      target_indexes[targets] = len(target_sets)
      target_sets.append(list(targets))
    site = result.site
    records.append({
      "site_id": site.site_id,
      "source": str(site.origin_file.relative_to(ROOT)),
      "line": site.origin_line,
      "mutator": site.mutator,
      "original_op": site.original_op,
      "mutated_op": site.mutated_op,
      "selected_test_set": target_indexes[targets],
      "selected_test_count": len(targets),
      "outcome": result.outcome,
      "test_sec": result.test_sec,
      "details": result.details,
      "failure_kind": result.failure_kind,
      "exit_code": result.exit_code,
      "stderr_tail": result.stderr_tail,
      "stdout_tail": result.stdout_tail,
      "unittest_output_tail": result.unittest_output_tail,
    })
  sources = {str(site.origin_file.relative_to(ROOT)): hashlib.sha256(site.origin_file.read_bytes()).hexdigest()
             for site in discovered_sites}
  artifact = {
    "schema_version": 2,
    "proven_equivalent": list(proven_equivalent),
    "safety_input_sha256": hashlib.sha256((ROOT / SAFETY_C_REL).read_bytes()).hexdigest(),
    "source_sha256": sources,
    "preprocessed_source_sha256": hashlib.sha256(preprocessed_source.encode()).hexdigest(),
    "mutation_runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "baseline_sec": baseline_sec,
    "discovered": len(discovered_sites),
    "pruned_build_incompatible": [{
      "site_id": site.site_id,
      "source": str(site.origin_file.relative_to(ROOT)),
      "line": site.origin_line,
      "mutator": site.mutator,
      "original_op": site.original_op,
      "mutated_op": site.mutated_op,
    } for site in discovered_sites if site.site_id in pruned_ids],
    "target_sets": target_sets,
    "results": records,
  }
  path.parent.mkdir(parents=True, exist_ok=True)
  temporary = None
  try:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=f".{path.name}.",
                                     suffix=".tmp", dir=path.parent, delete=False) as stream:
      temporary = Path(stream.name)
      json.dump(artifact, stream, indent=2, sort_keys=True)
      stream.write("\n")
      stream.flush()
      os.fsync(stream.fileno())
    os.replace(temporary, path)
    temporary = None
  finally:
    if temporary is not None:
      temporary.unlink(missing_ok=True)


def main():
  parser = argparse.ArgumentParser(description="Run strict safety mutation")
  parser.add_argument("-j", type=int, default=max((os.cpu_count() or 1) - 1, 1), help="parallel mutants to run")
  parser.add_argument("--max-mutants", type=int, default=0, help="optional limit for debugging (0 means all)")
  parser.add_argument("--list-only", action="store_true", help="list discovered candidates and exit")
  parser.add_argument("--verbose", action="store_true", help="print extra debug output")
  parser.add_argument("--results-json", type=Path, help="optional per-candidate result and diagnostic artifact")
  parser.add_argument("--verify-equivalence-artifact", type=Path, help=argparse.SUPPRESS)
  args = parser.parse_args()
  if args.verify_equivalence_artifact is not None:
    if args.list_only or args.results_json is not None or args.max_mutants:
      parser.error("equivalence verification is separate from execution")
    print(verify_equivalence_artifact(args.verify_equivalence_artifact), flush=True)
    return 0
  if args.list_only and args.results_json is not None:
    parser.error("--results-json requires a full mutation run")

  start = time.perf_counter()

  with tempfile.TemporaryDirectory(prefix="mutation-op-run-") as run_tmp_dir:
    preprocessed_file = Path(run_tmp_dir) / "safety_preprocessed.c"
    sites, mutator_counts, build_incompatible_ids, preprocessed_source = enumerate_sites(ROOT / SAFETY_C_REL, preprocessed_file)
    assert len(sites) > 0

    if args.max_mutants > 0:
      sites = sites[: args.max_mutants]
    discovered_sites = sites

    mutator_summary = ", ".join(f"{name} ({c})" for name in MUTATOR_FAMILIES if (c := mutator_counts.get(name, 0)) > 0)
    print(f"Found {len(sites)} unique candidates: {mutator_summary}", flush=True)
    if args.list_only:
      for site in sites:
        mutation = format_mutation(site.original_op, site.mutated_op)
        print(f"  #{site.site_id:03d} {site.origin_file.relative_to(ROOT)}:{site.origin_line} [{site.mutator}] {mutation}")
      return 0

    print(f"Running {len(sites)} mutants with {args.j} workers", flush=True)

    discovered_count = len(sites)
    selected_site_ids = {s.site_id for s in sites}
    build_incompatible_ids &= selected_site_ids
    pruned_compile_sites = len(build_incompatible_ids)
    proven_equivalent = prove_equivalent_divisions(preprocessed_source, sites, build_incompatible_ids)
    equivalent_ids = {record["site_id"] for record in proven_equivalent}
    sites = [site for site in sites if site.site_id not in equivalent_ids]
    print(f"Proven equivalent: {len(proven_equivalent)} closed unsigned division mutants", flush=True)
    if pruned_compile_sites > 0:
      sites = [s for s in sites if s.site_id not in build_incompatible_ids]
      print(f"Pruned {pruned_compile_sites} build-incompatible mutants from constant-expression contexts", flush=True)
    if not sites:
      print("Failed to build mutation library: all sites were pruned as build-incompatible", flush=True)
      return 2

    mutation_lib = Path(run_tmp_dir) / "libsafety_mutation.so"
    compile_mutated_library(preprocessed_source, sites, mutation_lib)

    # Discover all tests by importing modules in the main process.
    # Forked workers inherit these imports, eliminating per-worker import cost.
    catalog = _discover_test_catalog()

    # A mutant-induced assertion or exception counts as killed only after this
    # same complete test catalog has passed against the unmutated library.
    try:
      baseline_start = time.perf_counter()
      site_targets = require_full_baseline(catalog, sites, mutation_lib, args.verbose)
      baseline_sec = time.perf_counter() - baseline_start
    except RuntimeError as exc:
      print(f"Mutation baseline or target selection failed: {exc}", flush=True)
      return 2

    results = []
    counts = Counter()
    timeout_sec = max(60.0, min(300.0, baseline_sec * 4.0))
    print(f"Isolated mutant timeout: {timeout_sec:.1f}s (unmutated full baseline {baseline_sec:.1f}s)", flush=True)

    with ProcessPoolExecutor(max_workers=args.j, mp_context=multiprocessing.get_context("spawn")) as pool:
      future_map = {
        pool.submit(eval_mutant, site, site_targets[site.site_id], mutation_lib, args.verbose, timeout_sec): site for site in sites
      }
      print_live_status(render_progress(0, len(sites), 0, 0, 0, 0.0))
      try:
        for fut in as_completed(future_map):
          res = completed_mutant_result(fut, future_map[fut])
          results.append(res)
          counts[res.outcome] += 1
          elapsed_now = time.perf_counter() - start
          done = len(results) == len(sites)
          print_live_status(render_progress(len(results), len(sites), counts["killed"], counts["survived"],
                                            counts["infra_error"], elapsed_now), final=done)
      except Exception as exc:
        # An interrupted pool cannot establish which mutations changed behavior.
        completed_ids = {r.site.site_id for r in results}
        for site in sites:
          if site.site_id not in completed_ids:
            results.append(MutantResult(site, "infra_error", 0.0, f"pool interrupted: {type(exc).__name__}: {exc}"))
            counts["infra_error"] += 1
        elapsed_now = time.perf_counter() - start
        print_live_status(render_progress(len(results), len(sites), counts["killed"], counts["survived"], counts["infra_error"], elapsed_now), final=True)

    if args.results_json is not None:
      write_results_json(args.results_json, discovered_sites=discovered_sites,
                         pruned_ids=build_incompatible_ids, results=results, site_targets=site_targets,
                         preprocessed_source=preprocessed_source, baseline_sec=baseline_sec, proven_equivalent=proven_equivalent)

    survivors = sorted((r for r in results if r.outcome == "survived"), key=lambda r: r.site.site_id)
    if survivors:
      print("", flush=True)
      print(colorize("Surviving mutants", ANSI_RED), flush=True)
      for res in survivors:
        loc = f"{res.site.origin_file.relative_to(ROOT)}:{res.site.origin_line}"
        mutation = format_mutation(res.site.original_op, res.site.mutated_op)
        print(f"- #{res.site.site_id} {loc} [{res.site.mutator}] {mutation}", flush=True)
        print(format_site_snippet(res.site), flush=True)

    infra_results = sorted((r for r in results if r.outcome == "infra_error"), key=lambda r: r.site.site_id)
    if infra_results:
      print("", flush=True)
      print(colorize("Infra errors", ANSI_YELLOW), flush=True)
      for res in infra_results:
        loc = f"{res.site.origin_file.relative_to(ROOT)}:{res.site.origin_line}"
        detail = res.details.splitlines()[0] if res.details else "unknown error"
        print(f"- #{res.site.site_id} {loc}: {detail}", flush=True)

    elapsed = time.perf_counter() - start
    total_test_sec = sum(r.test_sec for r in results)
    print("", flush=True)
    print(colorize("Mutation summary", ANSI_BOLD), flush=True)
    print(f"  discovered: {discovered_count}", flush=True)
    print(f"  pruned_build_incompatible: {pruned_compile_sites}", flush=True)
    print(f"  proven_equivalent: {len(proven_equivalent)}", flush=True)
    print(f"  total: {len(sites)}", flush=True)
    print(f"  killed: {colorize(str(counts['killed']), ANSI_GREEN)}", flush=True)
    print(f"  survived: {colorize(str(counts['survived']), ANSI_RED)}", flush=True)
    print(f"  infra_error: {colorize(str(counts['infra_error']), ANSI_YELLOW)}", flush=True)
    print(f"  test_time_sum: {total_test_sec:.2f}s", flush=True)
    print(f"  avg_test_per_mutant: {total_test_sec / len(results):.3f}s", flush=True)
    print(f"  mutants_per_second: {len(sites) / elapsed:.2f}", flush=True)
    print(f"  elapsed: {elapsed:.2f}s", flush=True)

    if counts["infra_error"] > 0:
      return 2

    if survivors:
      return 1
    return 0


if __name__ == "__main__":
  raise SystemExit(main())
