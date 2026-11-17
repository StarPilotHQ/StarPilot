#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
cd $DIR

source ../../../setup.sh

# TODO: get UBSan and coverage without running the tests twice.
# Run UBSan separately so its checks do not count as coverage branches.
SAFETY_COVERAGE=0 python -m unittest discover -s .
SAFETY_COVERAGE=0 python -m unittest discover -s ../../bluepilot_lateral/safety/tests -t ../../..

# reset coverage data, including metadata from previous builds
rm -f ./libsafety/*.gcda ./libsafety/*.gcno

# run safety tests and generate coverage data
SAFETY_COVERAGE=1 python -m unittest discover -s .
# the BluePilot lateral library's safety tests, by directory: a loader here would put its classes in the
# cross-mode TX sweep, which reads every Test* class in this directory
SAFETY_COVERAGE=1 python -m unittest discover -s ../../bluepilot_lateral/safety/tests -t ../../..

# NOTE: we accept that these tools will have slight differences,
# and in return, we get to use the stock toolchain instead of
# installing LLVM on all users' machines
if [ "$(uname)" = "Darwin" ]; then
  GCOV_EXEC="llvm-cov gcov"
else
  GCOV_EXEC="gcov"
fi

# generate and open report
if [ "$1" == "--report" ]; then
  mkdir -p coverage-out
  gcovr -r ../ --gcov-executable "$GCOV_EXEC" --html-nested coverage-out/index.html
  sensible-browser coverage-out/index.html
fi

# test coverage
# the library's headers in a run of their own: its safety/ford.h shares modes/ford.h's file name, and one run
# credits one file's lines to the other
BP_GCOV="gcovr -r $DIR/../../.. --gcov-executable \"$GCOV_EXEC\" --fail-under-line=100 -f $DIR/../../bluepilot_lateral/safety/"
if ! BP_GCOV_OUTPUT="$(eval $BP_GCOV)"; then
  echo -e "FAILED:\n$BP_GCOV_OUTPUT"
  exit 1
fi
GCOV="gcovr -r $DIR/../ --gcov-executable \"$GCOV_EXEC\" -d --fail-under-line=100 -e ^libsafety"
if ! GCOV_OUTPUT="$(eval $GCOV)"; then
  echo -e "FAILED:\n$GCOV_OUTPUT"
  exit 1
else
  echo "SUCCESS: All checked files have 100% coverage!"
fi
