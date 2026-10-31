#!/usr/bin/env python3
import os
import time
import math
from dataclasses import dataclass
import numpy as np
from openpilot.cereal import log
from opendbc.car.interfaces import ACCEL_MIN, ACCEL_MAX
from openpilot.common.realtime import DT_MDL
from openpilot.common.swaglog import cloudlog
# WARNING: imports outside of constants will not trigger a rebuild
from openpilot.selfdrive.modeld.constants import index_function, ModelConstants
from openpilot.selfdrive.controls.radard import _LEAD_ACCEL_TAU, RADAR_TO_CAMERA

if __name__ == '__main__':  # generating code
  from acados.acados_template import AcadosModel, AcadosOcp, AcadosOcpSolver
else:
  from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib.c_generated_code.acados_ocp_solver_pyx import AcadosOcpSolverCython

from casadi import SX, vertcat

MODEL_NAME = 'long'
LONG_MPC_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORT_DIR = os.path.join(LONG_MPC_DIR, "c_generated_code")
JSON_FILE = os.path.join(LONG_MPC_DIR, "acados_ocp_long.json")

LongitudinalPlanSource = log.LongitudinalPlan.LongitudinalPlanSource
MPC_SOURCES = (LongitudinalPlanSource.lead0, LongitudinalPlanSource.lead1)

X_DIM = 3
U_DIM = 1
PARAM_DIM = 6
COST_E_DIM = 5
COST_DIM = COST_E_DIM + 1
CONSTR_DIM = 4

X_EGO_OBSTACLE_COST = 3.
X_EGO_COST = 0.
V_EGO_COST = 0.
A_EGO_COST = 0.
J_EGO_COST = 5.
A_CHANGE_COST = 200.
DANGER_ZONE_COST = 100.
CRASH_DISTANCE = .25
LEAD_DANGER_FACTOR = 0.75
LIMIT_COST = 1e6
ACADOS_SOLVER_TYPE = 'SQP_RTI'

# Fewer timestamps don't hurt performance and lead to
# much better convergence of the MPC with low iterations
N = 12
MAX_T = 10.0
T_IDXS_LST = [index_function(idx, max_val=MAX_T, max_idx=N) for idx in range(N+1)]

T_IDXS = np.array(T_IDXS_LST)
FCW_IDXS = T_IDXS < 5.0
T_DIFFS = np.diff(T_IDXS, prepend=[0.])
COMFORT_BRAKE = 2.5
STOP_DISTANCE = 6.0
MIN_X_LEAD_FACTOR = 0.5
LEAD_T_IDXS_MODEL = np.asarray(ModelConstants.LEAD_T_IDXS, dtype=np.float64)
MODEL_LEAD_TRAJECTORY_MAX_LEAD_BRAKE = 0.5
MODEL_LEAD_TRAJECTORY_MAX_CLOSING_TTC = 7.0

def should_trigger_planner_fcw(lead, v_ego: float) -> bool:
  if lead is None or not lead.present or float(lead.modelProb) <= 0.9:
    return False
  closing_speed = max(0.0, v_ego - float(lead.vLead))
  if closing_speed < 0.5:
    return False
  ttc = max(0.0, float(lead.dRel)) / closing_speed
  return ttc < 4.0


def build_model_lead_trajectory(model_lead, radar_lead, v_ego, model_v_ego=None):
  if model_lead is None or radar_lead is None or not bool(getattr(radar_lead, "present", False)):
    return None

  try:
    if not np.isfinite(float(model_lead.prob)) or float(model_lead.prob) <= 0.5:
      return None
    model_x = np.asarray(model_lead.x, dtype=np.float64)
    model_v = np.asarray(model_lead.v, dtype=np.float64)
    model_y = np.asarray(model_lead.y, dtype=np.float64)
    model_t = np.asarray(model_lead.t, dtype=np.float64)
  except (AttributeError, TypeError, ValueError):
    return None

  expected_len = len(LEAD_T_IDXS_MODEL)
  if any(value.shape != (expected_len,) for value in (model_x, model_v, model_y, model_t)):
    return None
  if (any(not np.all(np.isfinite(value)) for value in (model_x, model_v, model_y, model_t)) or
      not np.array_equal(model_t, LEAD_T_IDXS_MODEL) or not lead_horizon_matches(model_lead, radar_lead, v_ego, model_v_ego)):
    return None

  raw_d_rel = float(getattr(radar_lead, "dRel", float("nan")))
  raw_v_lead = float(getattr(radar_lead, "vLead", float("nan")))
  if not np.isfinite(raw_d_rel) or not np.isfinite(raw_v_lead):
    return None

  raw_accel = float(getattr(radar_lead, "aLeadK", float("nan")))
  if not np.isfinite(raw_accel) or not np.isfinite(v_ego):
    return None
  raw_lead_brake = max(0.0, -raw_accel)
  closing_speed = max(0.0, float(v_ego) - raw_v_lead)
  ttc = raw_d_rel / max(closing_speed, 1e-3) if closing_speed > 0.1 else float("inf")
  if (raw_lead_brake > MODEL_LEAD_TRAJECTORY_MAX_LEAD_BRAKE or
      (closing_speed > 0.75 and ttc < MODEL_LEAD_TRAJECTORY_MAX_CLOSING_TTC)):
    return None

  x_lead_traj = raw_d_rel + (model_x - model_x[0])
  v_lead_traj = raw_v_lead + (model_v - model_v[0])
  v_lead_traj = np.clip(v_lead_traj, 0.0, 1e8)

  v_ego = float(v_ego)
  min_x_lead = (MIN_X_LEAD_FACTOR * (v_ego + v_lead_traj[0])) * (v_ego - v_lead_traj[0]) / (-ACCEL_MIN * 2.0)
  x_lead_traj[0] = max(x_lead_traj[0], min_x_lead)

  x_lead_mpc = np.maximum.accumulate(np.interp(T_IDXS, LEAD_T_IDXS_MODEL, x_lead_traj))
  v_lead_mpc = np.interp(T_IDXS, LEAD_T_IDXS_MODEL, v_lead_traj)
  return np.column_stack((x_lead_mpc, v_lead_mpc))

def lead_horizon_matches(model_lead, lead, v_ego, model_v_ego):
  try:
    values = tuple(float(value) for value in (model_lead.prob, lead.modelProb, model_lead.x[0], model_lead.y[0],
      model_lead.v[0], lead.dRel, lead.yRel, lead.vLead, v_ego, model_v_ego))
    if not all(math.isfinite(value) for value in values):
      return False
    probability, matched_probability, model_x, model_y, model_v, distance, lateral, speed, ego, model_ego = values
    if not .5 < probability <= 1. or not .5 < matched_probability <= 1. or distance <= 0. or speed < 0.:
      return False
    expected_distance = model_x - RADAR_TO_CAMERA
    expected_speed = ego + model_v - model_ego
    if type(lead.radar) is not bool or type(lead.radarTrackId) is not int:
      return False
    if lead.radar:
      return bool(lead.radarTrackId >= 0 and
        abs(distance - expected_distance) < max(5., .25 * expected_distance) and
        abs(lateral + model_y) < 1. and abs(speed - expected_speed) < 2.)
    return bool(lead.radarTrackId == -1 and abs(distance - expected_distance) <= .001 and
      abs(lateral + model_y) <= .001 and math.isfinite(float(lead.vRel)) and
      abs(float(lead.vRel) - (model_v - model_ego)) <= .001)
  except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
    return False


def _model_lead_clock_pair_ns():
  clock = getattr(time, 'CLOCK_BOOTTIME', None)
  if clock is None:
    return None
  try:
    before = time.monotonic_ns()
    boot = time.clock_gettime_ns(clock)
    after = time.monotonic_ns()
  except OSError:
    return None
  return ((before + after) // 2, boot) if 0 <= after - before <= 1_000_000 else None


@dataclass(frozen=True)
class LeadHorizon:
  prob: float
  x: tuple[float, ...]
  y: tuple[float, ...]
  v: tuple[float, ...]
  t: tuple[float, ...]


@dataclass(frozen=True)
class HorizonVelocity:
  x: tuple[float, ...]


@dataclass(frozen=True)
class ModelHorizon:
  stamp: int
  receipt: int
  timestampEof: int
  velocity: HorizonVelocity
  leadsV3: tuple[LeadHorizon, ...]


class ModelLeadHistory:
  def __init__(self):
    self.frames = {}
    self.latest_stamp = 0

  def clear(self):
    self.frames.clear()
    self.latest_stamp = 0

  def observe(self, model, stamp, receipt):
    if stamp < self.latest_stamp:
      self.clear()
    if stamp == self.latest_stamp:
      return
    self.latest_stamp = stamp
    self.frames = {key: value for key, value in self.frames.items() if stamp - key <= 150_000_000}
    if len(model.leadsV3) < 2:
      return
    leads = []
    for index in range(2):
      lead = model.leadsV3[index]
      if any(len(getattr(lead, field)) != len(LEAD_T_IDXS_MODEL) for field in ('x', 'y', 'v', 't')):
        return
      leads.append(LeadHorizon(float(lead.prob), *(tuple(float(v) for v in getattr(lead, field)) for field in ('x', 'y', 'v', 't'))))
    self.frames[stamp] = ModelHorizon(stamp, receipt, int(model.timestampEof),
      HorizonVelocity((float(model.velocity.x[0]),)), tuple(leads))
    while len(self.frames) > 4:
      del self.frames[min(self.frames)]


def qualified_model_leads(sm, now_ns, history=None):
  try:
    pair = _model_lead_clock_pair_ns()
    if (type(now_ns) is not int or type(pair) is not tuple or len(pair) != 2 or
        any(type(value) is not int or value <= 0 for value in pair) or not 0 <= pair[0] - now_ns <= 150_000_000):
      return None
    for name in ('modelV2', 'radarState', 'carState'):
      if not all(getattr(sm, field, {}).get(name) is True for field in ('valid', 'alive', 'freq_ok')):
        return None
      stamp = sm.logMonoTime.get(name, 0)
      received = sm.recv_time.get(name, float('nan'))
      if type(received) not in (int, float) or not math.isfinite(received):
        return None
      receipt = int(received * 1e9)
      if type(stamp) is not int or not 0 < stamp <= receipt <= pair[0] or pair[0] - stamp > 150_000_000:
        return None
    model, radar, car = sm['modelV2'], sm['radarState'], sm['carState']
    eof = model.timestampEof
    eof_mono = eof - (pair[1] - pair[0])
    if (not car.canValid or car.canTimeout or any(radar.radarErrors.to_dict().values()) or
        radar.mdMonoTime <= 0 or radar.mdMonoTime > sm.logMonoTime['radarState'] or
        type(eof) is not int or not 0 < eof <= pair[1] or pair[1] - eof > 150_000_000 or
        not 0 < eof_mono <= sm.logMonoTime['modelV2'] or len(model.velocity.x) < 1 or
        not math.isfinite(float(model.velocity.x[0]))):
      return None
    if history is None:
      return model if radar.mdMonoTime == sm.logMonoTime['modelV2'] else None
    history.observe(model, sm.logMonoTime['modelV2'], int(sm.recv_time['modelV2'] * 1e9))
    selected = history.frames.get(int(radar.mdMonoTime))
    if selected is None:
      return None
    selected_eof_mono = selected.timestampEof - (pair[1] - pair[0])
    if (not 0 < selected.stamp <= selected.receipt <= pair[0] or pair[0] - selected.stamp > 150_000_000 or
        not 0 < selected.timestampEof <= pair[1] or pair[1] - selected.timestampEof > 150_000_000 or
        not 0 < selected_eof_mono <= selected.stamp):
      return None
    return selected
  except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError, OSError):
    return None


def get_jerk_factor(personality=log.LongitudinalPersonality.standard):
  if personality==log.LongitudinalPersonality.relaxed:
    return 1.0
  elif personality==log.LongitudinalPersonality.standard:
    return 1.0
  elif personality==log.LongitudinalPersonality.aggressive:
    return 0.5
  else:
    raise NotImplementedError("Longitudinal personality not supported")


def get_T_FOLLOW(personality=log.LongitudinalPersonality.standard):
  if personality==log.LongitudinalPersonality.relaxed:
    return 1.75
  elif personality==log.LongitudinalPersonality.standard:
    return 1.45
  elif personality==log.LongitudinalPersonality.aggressive:
    return 1.25
  else:
    raise NotImplementedError("Longitudinal personality not supported")

def get_stopped_equivalence_factor(v_lead):
  return (v_lead**2) / (2 * COMFORT_BRAKE)

def get_safe_obstacle_distance(v_ego, t_follow):
  return (v_ego**2) / (2 * COMFORT_BRAKE) + t_follow * v_ego + STOP_DISTANCE

def gen_long_model():
  model = AcadosModel()
  model.name = MODEL_NAME

  # states
  x_ego, v_ego, a_ego = SX.sym('x_ego'), SX.sym('v_ego'), SX.sym('a_ego')
  model.x = vertcat(x_ego, v_ego, a_ego)

  # controls
  j_ego = SX.sym('j_ego')
  model.u = vertcat(j_ego)

  # xdot
  x_ego_dot = SX.sym('x_ego_dot')
  v_ego_dot = SX.sym('v_ego_dot')
  a_ego_dot = SX.sym('a_ego_dot')
  model.xdot = vertcat(x_ego_dot, v_ego_dot, a_ego_dot)

  # runtime parameters
  a_min = SX.sym('a_min')
  a_max = SX.sym('a_max')
  x_obstacle = SX.sym('x_obstacle')
  a_prev = SX.sym('a_prev')
  lead_t_follow = SX.sym('lead_t_follow')
  lead_danger_factor = SX.sym('lead_danger_factor')
  model.p = vertcat(a_min, a_max, x_obstacle, a_prev, lead_t_follow, lead_danger_factor)

  # dynamics model
  f_expl = vertcat(v_ego, a_ego, j_ego)
  model.f_impl_expr = model.xdot - f_expl
  model.f_expl_expr = f_expl
  return model

def gen_long_ocp():
  ocp = AcadosOcp()
  ocp.model = gen_long_model()

  Tf = T_IDXS[-1]

  # set dimensions
  ocp.dims.N = N

  # set cost module
  ocp.cost.cost_type = 'NONLINEAR_LS'
  ocp.cost.cost_type_e = 'NONLINEAR_LS'

  QR = np.zeros((COST_DIM, COST_DIM))
  Q = np.zeros((COST_E_DIM, COST_E_DIM))

  ocp.cost.W = QR
  ocp.cost.W_e = Q

  x_ego, v_ego, a_ego = ocp.model.x[0], ocp.model.x[1], ocp.model.x[2]
  j_ego = ocp.model.u[0]

  a_min, a_max = ocp.model.p[0], ocp.model.p[1]
  x_obstacle = ocp.model.p[2]
  a_prev = ocp.model.p[3]
  lead_t_follow = ocp.model.p[4]
  lead_danger_factor = ocp.model.p[5]

  ocp.cost.yref = np.zeros((COST_DIM, ))
  ocp.cost.yref_e = np.zeros((COST_E_DIM, ))

  desired_dist_comfort = get_safe_obstacle_distance(v_ego, lead_t_follow)

  # The main cost in normal operation is how close you are to the "desired" distance
  # from an obstacle at every timestep. This obstacle can be a lead car
  # or other object. In e2e mode we can use x_position targets as a cost
  # instead.
  costs = [((x_obstacle - x_ego) - (desired_dist_comfort)) / (v_ego + 10.),
           x_ego,
           v_ego,
           a_ego,
           a_ego - a_prev,
           j_ego]
  ocp.model.cost_y_expr = vertcat(*costs)
  ocp.model.cost_y_expr_e = vertcat(*costs[:-1])

  # Constraints on speed, acceleration and desired distance to
  # the obstacle, which is treated as a slack constraint so it
  # behaves like an asymmetrical cost.
  constraints = vertcat(v_ego,
                        (a_ego - a_min),
                        (a_max - a_ego),
                        ((x_obstacle - x_ego) - lead_danger_factor * (desired_dist_comfort)) / (v_ego + 10.))
  ocp.model.con_h_expr = constraints
  ocp.model.con_h_expr_e = constraints

  x0 = np.zeros(X_DIM)
  ocp.constraints.x0 = x0
  ocp.parameter_values = np.array([-1.2, 1.2, 0.0, 0.0, get_T_FOLLOW(), LEAD_DANGER_FACTOR])


  # We put all constraint cost weights to 0 and only set them at runtime
  cost_weights = np.zeros(CONSTR_DIM)
  ocp.cost.zl = cost_weights
  ocp.cost.Zl = cost_weights
  ocp.cost.Zu = cost_weights
  ocp.cost.zu = cost_weights
  ocp.cost.zl_e = cost_weights
  ocp.cost.Zl_e = cost_weights
  ocp.cost.Zu_e = cost_weights
  ocp.cost.zu_e = cost_weights

  ocp.constraints.lh = np.zeros(CONSTR_DIM)
  ocp.constraints.uh = 1e4*np.ones(CONSTR_DIM)
  ocp.constraints.idxsh = np.arange(CONSTR_DIM)
  ocp.constraints.lh_e = np.zeros(CONSTR_DIM)
  ocp.constraints.uh_e = 1e4*np.ones(CONSTR_DIM)
  ocp.constraints.idxsh_e = np.arange(CONSTR_DIM)

  # The HPIPM solver can give decent solutions even when it is stopped early
  # Which is critical for our purpose where compute time is strictly bounded
  # We use HPIPM in the SPEED_ABS mode, which ensures fastest runtime. This
  # does not cause issues since the problem is well bounded.
  ocp.solver_options.qp_solver = 'PARTIAL_CONDENSING_HPIPM'
  ocp.solver_options.hessian_approx = 'GAUSS_NEWTON'
  ocp.solver_options.integrator_type = 'ERK'
  ocp.solver_options.nlp_solver_type = ACADOS_SOLVER_TYPE
  ocp.solver_options.qp_solver_cond_N = 1

  # More iterations take too much time and less lead to inaccurate convergence in
  # some situations. Ideally we would run just 1 iteration to ensure fixed runtime.
  ocp.solver_options.qp_solver_iter_max = 10
  ocp.solver_options.qp_tol = 1e-3

  # set prediction horizon
  ocp.solver_options.tf = Tf
  ocp.solver_options.shooting_nodes = T_IDXS

  ocp.code_export_directory = EXPORT_DIR
  return ocp


class LongitudinalMpc:
  def __init__(self, dt=DT_MDL):
    self.dt = dt
    self.solver = AcadosOcpSolverCython(MODEL_NAME, ACADOS_SOLVER_TYPE, N)
    self.reset()
    self.source = LongitudinalPlanSource.cruise

  def reset(self):
    self._cached_cost_weights = None
    self.solver.reset()

    self.x_sol = np.zeros((N+1, X_DIM))
    self.u_sol = np.zeros((N, 1))
    self.v_solution = np.zeros(N+1)
    self.a_solution = np.zeros(N+1)
    self.j_solution = np.zeros(N)
    self.a_prev = np.array(self.a_solution)
    self.yref = np.zeros((N+1, COST_DIM))

    for i in range(N):
      self.solver.cost_set(i, "yref", self.yref[i])
    self.solver.cost_set(N, "yref", self.yref[N][:COST_E_DIM])

    self.params = np.zeros((N+1, PARAM_DIM))
    for i in range(N+1):
      self.solver.set(i, 'x', np.zeros(X_DIM))

    self.last_cloudlog_t = 0
    self.crash_cnt = 0.0
    self.solution_status = 0
    # timers
    self.solve_time = 0.0
    self.x0 = np.zeros(X_DIM)
    self.set_weights()

  def set_cost_weights(self, cost_weights, constraint_cost_weights):
    key = (tuple(cost_weights), tuple(constraint_cost_weights))
    if self._cached_cost_weights == key:
      return
    self._cached_cost_weights = None
    W = np.asfortranarray(np.diag(cost_weights))
    for i in range(N):
      # TODO don't hardcode A_CHANGE_COST idx
      # reduce the cost on (a-a_prev) later in the horizon.
      W[4,4] = cost_weights[4] * np.interp(T_IDXS[i], [0.0, 1.0, 2.0], [1.0, 1.0, 0.0])
      self.solver.cost_set(i, 'W', W)
    # Setting the slice without the copy make the array not contiguous,
    # causing issues with the C interface.
    self.solver.cost_set(N, 'W', np.copy(W[:COST_E_DIM, :COST_E_DIM]))

    # Set L2 slack cost on lower bound constraints
    Zl = np.array(constraint_cost_weights)
    for i in range(N+1):
      self.solver.cost_set(i, 'Zl', Zl)
    self._cached_cost_weights = key

  def set_weights(self, prev_accel_constraint=True, personality=log.LongitudinalPersonality.standard,
                  *, acceleration_jerk: float | None = None, speed_jerk: float | None = None,
                  danger_jerk: float | None = None, stop_jerk_scale: float = 1.0,
                  acceleration_change_scale: float = 1.0):
    jerk_factor = get_jerk_factor(personality)
    if acceleration_jerk is not None and math.isfinite(acceleration_jerk) and 0.25 <= acceleration_jerk <= 2.0:
      a_change_factor = acceleration_jerk
    else:
      a_change_factor = jerk_factor
    if speed_jerk is not None and math.isfinite(speed_jerk) and 0.25 <= speed_jerk <= 2.0:
      speed_factor = speed_jerk
    else:
      speed_factor = jerk_factor
    if danger_jerk is not None and math.isfinite(danger_jerk) and 0.25 <= danger_jerk <= 2.0:
      danger_factor = danger_jerk
    else:
      danger_factor = 1.0
    a_change_factor *= acceleration_change_scale if (type(acceleration_change_scale) in (int, float) and
                                                    math.isfinite(acceleration_change_scale) and
                                                    1.0 <= acceleration_change_scale <= 2.0) else 1.0
    a_change_factor *= stop_jerk_scale if stop_jerk_scale in (0.32, 0.8) else 1.0
    a_change_cost = A_CHANGE_COST if prev_accel_constraint else 0
    cost_weights = [X_EGO_OBSTACLE_COST, X_EGO_COST, V_EGO_COST, A_EGO_COST, a_change_factor * a_change_cost, speed_factor * J_EGO_COST]
    constraint_cost_weights = [LIMIT_COST, LIMIT_COST, LIMIT_COST, danger_factor * DANGER_ZONE_COST]
    self.set_cost_weights(cost_weights, constraint_cost_weights)

  def set_cur_state(self, v, a):
    v_prev = self.x0[1]
    self.x0[1] = v
    self.x0[2] = a
    if abs(v_prev - v) > 2.:  # probably only helps if v < v_prev
      for i in range(N+1):
        self.solver.set(i, 'x', self.x0)

  @staticmethod
  def extrapolate_lead(x_lead, v_lead, a_lead, a_lead_tau):
    a_lead_traj = a_lead * np.exp(-a_lead_tau * (T_IDXS**2)/2.)
    v_lead_traj = np.clip(v_lead + np.cumsum(T_DIFFS * a_lead_traj), 0.0, 1e8)
    x_lead_traj = x_lead + np.cumsum(T_DIFFS * v_lead_traj)
    lead_xv = np.column_stack((x_lead_traj, v_lead_traj))
    return lead_xv

  def process_lead(self, lead, model_lead=None, *, model_v_ego=None):
    v_ego = self.x0[1]
    model_path = build_model_lead_trajectory(model_lead, lead, v_ego, model_v_ego)
    if model_path is not None:
      return model_path
    if lead is not None and lead.present:
      x_lead = lead.dRel
      v_lead = lead.vLead
      a_lead = lead.aLeadK
      a_lead_tau = lead.aLeadTau
    else:
      # Fake a fast lead car, so mpc can keep running in the same mode
      x_lead = 50.0
      v_lead = v_ego + 10.0
      a_lead = 0.0
      a_lead_tau = _LEAD_ACCEL_TAU

    # MPC will not converge if immediate crash is expected
    # Clip lead distance to what is still possible to brake for
    min_x_lead = MIN_X_LEAD_FACTOR * (v_ego + v_lead) * (v_ego - v_lead) / (-ACCEL_MIN * 2)
    x_lead = np.clip(x_lead, min_x_lead, 1e8)
    v_lead = np.clip(v_lead, 0.0, 1e8)
    a_lead = np.clip(a_lead, -10., 5.)
    lead_xv = self.extrapolate_lead(x_lead, v_lead, a_lead, a_lead_tau)
    return lead_xv

  def update(self, radarstate, personality=log.LongitudinalPersonality.standard,
             *, follow_seconds: float | None = None, acceleration_max: float | None = None, stop_line_m: float | None = None,
             modelV2=None, extra_stopped_gap_m: float = 0.0):
    t_follow = get_T_FOLLOW(personality)
    if follow_seconds is not None and math.isfinite(follow_seconds) and 0.5 <= follow_seconds <= 3.0:
      t_follow = follow_seconds

    model_leads = getattr(modelV2, "leadsV3", ()) if modelV2 is not None else ()
    velocity = getattr(getattr(modelV2, 'velocity', None), 'x', ()) if modelV2 is not None else ()
    model_v_ego = velocity[0] if len(velocity) else None
    lead_xv_0 = self.process_lead(radarstate.leadOne, model_leads[0] if len(model_leads) > 0 else None, model_v_ego=model_v_ego)
    lead_xv_1 = self.process_lead(radarstate.leadTwo, model_leads[1] if len(model_leads) > 1 else None, model_v_ego=model_v_ego)

    # To estimate a safe distance from a moving lead, we calculate how much stopping
    # distance that lead needs as a minimum. We can add that to the current distance
    # and then treat that as a stopped car/obstacle at this new distance.
    lead_0_obstacle = lead_xv_0[:,0] + get_stopped_equivalence_factor(lead_xv_0[:,1])
    lead_1_obstacle = lead_xv_1[:,0] + get_stopped_equivalence_factor(lead_xv_1[:,1])

    if type(extra_stopped_gap_m) in (int, float) and math.isfinite(extra_stopped_gap_m) and 0.0 < extra_stopped_gap_m <= 3.048:
      # Shift only lead obstacles; physical trajectories still own collision warnings.
      if radarstate.leadOne.present:
        lead_0_obstacle -= extra_stopped_gap_m * np.clip(1.0 - lead_xv_0[:, 1] / 5.0, 0.0, 1.0)
      if radarstate.leadTwo.present:
        lead_1_obstacle -= extra_stopped_gap_m * np.clip(1.0 - lead_xv_1[:, 1] / 5.0, 0.0, 1.0)

    x_obstacles = np.column_stack([lead_0_obstacle, lead_1_obstacle])
    self.source = MPC_SOURCES[np.argmin(x_obstacles[0])]
    if stop_line_m is not None and math.isfinite(stop_line_m) and 0.0 <= stop_line_m <= 500.0:
      stop_obstacle = stop_line_m + STOP_DISTANCE
      if stop_obstacle < np.min(x_obstacles[0]):
        self.source = LongitudinalPlanSource.cruise
      x_obstacles = np.column_stack((x_obstacles, np.full(N + 1, stop_obstacle)))

    self.yref[:,:] = 0.0
    for i in range(N):
      self.solver.set(i, "yref", self.yref[i])
    self.solver.set(N, "yref", self.yref[N][:COST_E_DIM])

    self.params[:,0] = ACCEL_MIN
    bounded_accel = (acceleration_max is not None and math.isfinite(acceleration_max) and
                     0.0 <= acceleration_max <= ACCEL_MAX)
    self.params[:,1] = min(ACCEL_MAX, acceleration_max) if bounded_accel else ACCEL_MAX
    self.params[:,2] = np.min(x_obstacles, axis=1)
    self.params[:,3] = np.copy(self.a_prev)
    self.params[:,4] = t_follow
    self.params[:,5] = LEAD_DANGER_FACTOR

    self.run()
    if (np.any(lead_xv_0[FCW_IDXS,0] - self.x_sol[FCW_IDXS,0] < CRASH_DISTANCE) and
            radarstate.leadOne.modelProb > 0.9):
      self.crash_cnt += 1
    else:
      self.crash_cnt = 0

  def run(self):
    for i in range(N+1):
      self.solver.set(i, 'p', self.params[i])
    self.solver.constraints_set(0, "lbx", self.x0)
    self.solver.constraints_set(0, "ubx", self.x0)

    self.solution_status = self.solver.solve()
    self.solve_time = float(self.solver.get_stats('time_tot')[0])

    for i in range(N+1):
      self.x_sol[i] = self.solver.get(i, 'x')
    for i in range(N):
      self.u_sol[i] = self.solver.get(i, 'u')

    self.v_solution = self.x_sol[:,1]
    self.a_solution = self.x_sol[:,2]
    self.j_solution = self.u_sol[:,0]

    self.a_prev = np.interp(T_IDXS + self.dt, T_IDXS, self.a_solution)

    t = time.monotonic()
    if self.solution_status != 0:
      if t > self.last_cloudlog_t + 5.0:
        self.last_cloudlog_t = t
        cloudlog.warning(f"Long mpc reset, solution_status: {self.solution_status}")
      self.reset()


if __name__ == "__main__":
  ocp = gen_long_ocp()
  AcadosOcpSolver.generate(ocp, json_file=JSON_FILE)
