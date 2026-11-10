#include "car.h"

namespace {
#define DIM 9
#define EDIM 9
#define MEDIM 9
typedef void (*Hfun)(double *, double *, double *);

double mass;

void set_mass(double x){ mass = x;}

double rotational_inertia;

void set_rotational_inertia(double x){ rotational_inertia = x;}

double center_to_front;

void set_center_to_front(double x){ center_to_front = x;}

double center_to_rear;

void set_center_to_rear(double x){ center_to_rear = x;}

double stiffness_front;

void set_stiffness_front(double x){ stiffness_front = x;}

double stiffness_rear;

void set_stiffness_rear(double x){ stiffness_rear = x;}
const static double MAHA_THRESH_25 = 3.8414588206941227;
const static double MAHA_THRESH_24 = 5.991464547107981;
const static double MAHA_THRESH_30 = 3.8414588206941227;
const static double MAHA_THRESH_26 = 3.8414588206941227;
const static double MAHA_THRESH_27 = 3.8414588206941227;
const static double MAHA_THRESH_29 = 3.8414588206941227;
const static double MAHA_THRESH_28 = 3.8414588206941227;
const static double MAHA_THRESH_31 = 3.8414588206941227;

/******************************************************************************
 *                      Code generated with SymPy 1.14.0                      *
 *                                                                            *
 *              See http://www.sympy.org/ for more information.               *
 *                                                                            *
 *                         This file is part of 'ekf'                         *
 ******************************************************************************/
void err_fun(double *nom_x, double *delta_x, double *out_1014334527327209848) {
   out_1014334527327209848[0] = delta_x[0] + nom_x[0];
   out_1014334527327209848[1] = delta_x[1] + nom_x[1];
   out_1014334527327209848[2] = delta_x[2] + nom_x[2];
   out_1014334527327209848[3] = delta_x[3] + nom_x[3];
   out_1014334527327209848[4] = delta_x[4] + nom_x[4];
   out_1014334527327209848[5] = delta_x[5] + nom_x[5];
   out_1014334527327209848[6] = delta_x[6] + nom_x[6];
   out_1014334527327209848[7] = delta_x[7] + nom_x[7];
   out_1014334527327209848[8] = delta_x[8] + nom_x[8];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_3206419511188405682) {
   out_3206419511188405682[0] = -nom_x[0] + true_x[0];
   out_3206419511188405682[1] = -nom_x[1] + true_x[1];
   out_3206419511188405682[2] = -nom_x[2] + true_x[2];
   out_3206419511188405682[3] = -nom_x[3] + true_x[3];
   out_3206419511188405682[4] = -nom_x[4] + true_x[4];
   out_3206419511188405682[5] = -nom_x[5] + true_x[5];
   out_3206419511188405682[6] = -nom_x[6] + true_x[6];
   out_3206419511188405682[7] = -nom_x[7] + true_x[7];
   out_3206419511188405682[8] = -nom_x[8] + true_x[8];
}
void H_mod_fun(double *state, double *out_992849709061666640) {
   out_992849709061666640[0] = 1.0;
   out_992849709061666640[1] = 0.0;
   out_992849709061666640[2] = 0.0;
   out_992849709061666640[3] = 0.0;
   out_992849709061666640[4] = 0.0;
   out_992849709061666640[5] = 0.0;
   out_992849709061666640[6] = 0.0;
   out_992849709061666640[7] = 0.0;
   out_992849709061666640[8] = 0.0;
   out_992849709061666640[9] = 0.0;
   out_992849709061666640[10] = 1.0;
   out_992849709061666640[11] = 0.0;
   out_992849709061666640[12] = 0.0;
   out_992849709061666640[13] = 0.0;
   out_992849709061666640[14] = 0.0;
   out_992849709061666640[15] = 0.0;
   out_992849709061666640[16] = 0.0;
   out_992849709061666640[17] = 0.0;
   out_992849709061666640[18] = 0.0;
   out_992849709061666640[19] = 0.0;
   out_992849709061666640[20] = 1.0;
   out_992849709061666640[21] = 0.0;
   out_992849709061666640[22] = 0.0;
   out_992849709061666640[23] = 0.0;
   out_992849709061666640[24] = 0.0;
   out_992849709061666640[25] = 0.0;
   out_992849709061666640[26] = 0.0;
   out_992849709061666640[27] = 0.0;
   out_992849709061666640[28] = 0.0;
   out_992849709061666640[29] = 0.0;
   out_992849709061666640[30] = 1.0;
   out_992849709061666640[31] = 0.0;
   out_992849709061666640[32] = 0.0;
   out_992849709061666640[33] = 0.0;
   out_992849709061666640[34] = 0.0;
   out_992849709061666640[35] = 0.0;
   out_992849709061666640[36] = 0.0;
   out_992849709061666640[37] = 0.0;
   out_992849709061666640[38] = 0.0;
   out_992849709061666640[39] = 0.0;
   out_992849709061666640[40] = 1.0;
   out_992849709061666640[41] = 0.0;
   out_992849709061666640[42] = 0.0;
   out_992849709061666640[43] = 0.0;
   out_992849709061666640[44] = 0.0;
   out_992849709061666640[45] = 0.0;
   out_992849709061666640[46] = 0.0;
   out_992849709061666640[47] = 0.0;
   out_992849709061666640[48] = 0.0;
   out_992849709061666640[49] = 0.0;
   out_992849709061666640[50] = 1.0;
   out_992849709061666640[51] = 0.0;
   out_992849709061666640[52] = 0.0;
   out_992849709061666640[53] = 0.0;
   out_992849709061666640[54] = 0.0;
   out_992849709061666640[55] = 0.0;
   out_992849709061666640[56] = 0.0;
   out_992849709061666640[57] = 0.0;
   out_992849709061666640[58] = 0.0;
   out_992849709061666640[59] = 0.0;
   out_992849709061666640[60] = 1.0;
   out_992849709061666640[61] = 0.0;
   out_992849709061666640[62] = 0.0;
   out_992849709061666640[63] = 0.0;
   out_992849709061666640[64] = 0.0;
   out_992849709061666640[65] = 0.0;
   out_992849709061666640[66] = 0.0;
   out_992849709061666640[67] = 0.0;
   out_992849709061666640[68] = 0.0;
   out_992849709061666640[69] = 0.0;
   out_992849709061666640[70] = 1.0;
   out_992849709061666640[71] = 0.0;
   out_992849709061666640[72] = 0.0;
   out_992849709061666640[73] = 0.0;
   out_992849709061666640[74] = 0.0;
   out_992849709061666640[75] = 0.0;
   out_992849709061666640[76] = 0.0;
   out_992849709061666640[77] = 0.0;
   out_992849709061666640[78] = 0.0;
   out_992849709061666640[79] = 0.0;
   out_992849709061666640[80] = 1.0;
}
void f_fun(double *state, double dt, double *out_7284810420003339157) {
   out_7284810420003339157[0] = state[0];
   out_7284810420003339157[1] = state[1];
   out_7284810420003339157[2] = state[2];
   out_7284810420003339157[3] = state[3];
   out_7284810420003339157[4] = state[4];
   out_7284810420003339157[5] = dt*((-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]))*state[6] - 9.8100000000000005*state[8] + stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*state[1]) + (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*state[4])) + state[5];
   out_7284810420003339157[6] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*state[4])) + state[6];
   out_7284810420003339157[7] = state[7];
   out_7284810420003339157[8] = state[8];
}
void F_fun(double *state, double dt, double *out_8279640069865100833) {
   out_8279640069865100833[0] = 1;
   out_8279640069865100833[1] = 0;
   out_8279640069865100833[2] = 0;
   out_8279640069865100833[3] = 0;
   out_8279640069865100833[4] = 0;
   out_8279640069865100833[5] = 0;
   out_8279640069865100833[6] = 0;
   out_8279640069865100833[7] = 0;
   out_8279640069865100833[8] = 0;
   out_8279640069865100833[9] = 0;
   out_8279640069865100833[10] = 1;
   out_8279640069865100833[11] = 0;
   out_8279640069865100833[12] = 0;
   out_8279640069865100833[13] = 0;
   out_8279640069865100833[14] = 0;
   out_8279640069865100833[15] = 0;
   out_8279640069865100833[16] = 0;
   out_8279640069865100833[17] = 0;
   out_8279640069865100833[18] = 0;
   out_8279640069865100833[19] = 0;
   out_8279640069865100833[20] = 1;
   out_8279640069865100833[21] = 0;
   out_8279640069865100833[22] = 0;
   out_8279640069865100833[23] = 0;
   out_8279640069865100833[24] = 0;
   out_8279640069865100833[25] = 0;
   out_8279640069865100833[26] = 0;
   out_8279640069865100833[27] = 0;
   out_8279640069865100833[28] = 0;
   out_8279640069865100833[29] = 0;
   out_8279640069865100833[30] = 1;
   out_8279640069865100833[31] = 0;
   out_8279640069865100833[32] = 0;
   out_8279640069865100833[33] = 0;
   out_8279640069865100833[34] = 0;
   out_8279640069865100833[35] = 0;
   out_8279640069865100833[36] = 0;
   out_8279640069865100833[37] = 0;
   out_8279640069865100833[38] = 0;
   out_8279640069865100833[39] = 0;
   out_8279640069865100833[40] = 1;
   out_8279640069865100833[41] = 0;
   out_8279640069865100833[42] = 0;
   out_8279640069865100833[43] = 0;
   out_8279640069865100833[44] = 0;
   out_8279640069865100833[45] = dt*(stiffness_front*(-state[2] - state[3] + state[7])/(mass*state[1]) + (-stiffness_front - stiffness_rear)*state[5]/(mass*state[4]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[6]/(mass*state[4]));
   out_8279640069865100833[46] = -dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*pow(state[1], 2));
   out_8279640069865100833[47] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_8279640069865100833[48] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_8279640069865100833[49] = dt*((-1 - (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*pow(state[4], 2)))*state[6] - (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*pow(state[4], 2)));
   out_8279640069865100833[50] = dt*(-stiffness_front*state[0] - stiffness_rear*state[0])/(mass*state[4]) + 1;
   out_8279640069865100833[51] = dt*(-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]));
   out_8279640069865100833[52] = dt*stiffness_front*state[0]/(mass*state[1]);
   out_8279640069865100833[53] = -9.8100000000000005*dt;
   out_8279640069865100833[54] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front - pow(center_to_rear, 2)*stiffness_rear)*state[6]/(rotational_inertia*state[4]));
   out_8279640069865100833[55] = -center_to_front*dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*pow(state[1], 2));
   out_8279640069865100833[56] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_8279640069865100833[57] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_8279640069865100833[58] = dt*(-(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*pow(state[4], 2)) - (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*pow(state[4], 2)));
   out_8279640069865100833[59] = dt*(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(rotational_inertia*state[4]);
   out_8279640069865100833[60] = dt*(-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])/(rotational_inertia*state[4]) + 1;
   out_8279640069865100833[61] = center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_8279640069865100833[62] = 0;
   out_8279640069865100833[63] = 0;
   out_8279640069865100833[64] = 0;
   out_8279640069865100833[65] = 0;
   out_8279640069865100833[66] = 0;
   out_8279640069865100833[67] = 0;
   out_8279640069865100833[68] = 0;
   out_8279640069865100833[69] = 0;
   out_8279640069865100833[70] = 1;
   out_8279640069865100833[71] = 0;
   out_8279640069865100833[72] = 0;
   out_8279640069865100833[73] = 0;
   out_8279640069865100833[74] = 0;
   out_8279640069865100833[75] = 0;
   out_8279640069865100833[76] = 0;
   out_8279640069865100833[77] = 0;
   out_8279640069865100833[78] = 0;
   out_8279640069865100833[79] = 0;
   out_8279640069865100833[80] = 1;
}
void h_25(double *state, double *unused, double *out_8642135170742394965) {
   out_8642135170742394965[0] = state[6];
}
void H_25(double *state, double *unused, double *out_8200338516850570597) {
   out_8200338516850570597[0] = 0;
   out_8200338516850570597[1] = 0;
   out_8200338516850570597[2] = 0;
   out_8200338516850570597[3] = 0;
   out_8200338516850570597[4] = 0;
   out_8200338516850570597[5] = 0;
   out_8200338516850570597[6] = 1;
   out_8200338516850570597[7] = 0;
   out_8200338516850570597[8] = 0;
}
void h_24(double *state, double *unused, double *out_9197263242363141624) {
   out_9197263242363141624[0] = state[4];
   out_9197263242363141624[1] = state[5];
}
void H_24(double *state, double *unused, double *out_1027726669218624628) {
   out_1027726669218624628[0] = 0;
   out_1027726669218624628[1] = 0;
   out_1027726669218624628[2] = 0;
   out_1027726669218624628[3] = 0;
   out_1027726669218624628[4] = 1;
   out_1027726669218624628[5] = 0;
   out_1027726669218624628[6] = 0;
   out_1027726669218624628[7] = 0;
   out_1027726669218624628[8] = 0;
   out_1027726669218624628[9] = 0;
   out_1027726669218624628[10] = 0;
   out_1027726669218624628[11] = 0;
   out_1027726669218624628[12] = 0;
   out_1027726669218624628[13] = 0;
   out_1027726669218624628[14] = 1;
   out_1027726669218624628[15] = 0;
   out_1027726669218624628[16] = 0;
   out_1027726669218624628[17] = 0;
}
void h_30(double *state, double *unused, double *out_8720849294696219296) {
   out_8720849294696219296[0] = state[4];
}
void H_30(double *state, double *unused, double *out_5718709226731372821) {
   out_5718709226731372821[0] = 0;
   out_5718709226731372821[1] = 0;
   out_5718709226731372821[2] = 0;
   out_5718709226731372821[3] = 0;
   out_5718709226731372821[4] = 1;
   out_5718709226731372821[5] = 0;
   out_5718709226731372821[6] = 0;
   out_5718709226731372821[7] = 0;
   out_5718709226731372821[8] = 0;
}
void h_26(double *state, double *unused, double *out_4724221511352486675) {
   out_4724221511352486675[0] = state[7];
}
void H_26(double *state, double *unused, double *out_6504902237984924795) {
   out_6504902237984924795[0] = 0;
   out_6504902237984924795[1] = 0;
   out_6504902237984924795[2] = 0;
   out_6504902237984924795[3] = 0;
   out_6504902237984924795[4] = 0;
   out_6504902237984924795[5] = 0;
   out_6504902237984924795[6] = 0;
   out_6504902237984924795[7] = 1;
   out_6504902237984924795[8] = 0;
}
void h_27(double *state, double *unused, double *out_2893523921247530795) {
   out_2893523921247530795[0] = state[3];
}
void H_27(double *state, double *unused, double *out_7942303297915316038) {
   out_7942303297915316038[0] = 0;
   out_7942303297915316038[1] = 0;
   out_7942303297915316038[2] = 0;
   out_7942303297915316038[3] = 1;
   out_7942303297915316038[4] = 0;
   out_7942303297915316038[5] = 0;
   out_7942303297915316038[6] = 0;
   out_7942303297915316038[7] = 0;
   out_7942303297915316038[8] = 0;
}
void h_29(double *state, double *unused, double *out_2618329858963024906) {
   out_2618329858963024906[0] = state[1];
}
void H_29(double *state, double *unused, double *out_6228940571045765005) {
   out_6228940571045765005[0] = 0;
   out_6228940571045765005[1] = 1;
   out_6228940571045765005[2] = 0;
   out_6228940571045765005[3] = 0;
   out_6228940571045765005[4] = 0;
   out_6228940571045765005[5] = 0;
   out_6228940571045765005[6] = 0;
   out_6228940571045765005[7] = 0;
   out_6228940571045765005[8] = 0;
}
void h_28(double *state, double *unused, double *out_8550457448668955042) {
   out_8550457448668955042[0] = state[0];
}
void H_28(double *state, double *unused, double *out_1146541553976234431) {
   out_1146541553976234431[0] = 1;
   out_1146541553976234431[1] = 0;
   out_1146541553976234431[2] = 0;
   out_1146541553976234431[3] = 0;
   out_1146541553976234431[4] = 0;
   out_1146541553976234431[5] = 0;
   out_1146541553976234431[6] = 0;
   out_1146541553976234431[7] = 0;
   out_1146541553976234431[8] = 0;
}
void h_31(double *state, double *unused, double *out_7603820894445690215) {
   out_7603820894445690215[0] = state[8];
}
void H_31(double *state, double *unused, double *out_8169692554973610169) {
   out_8169692554973610169[0] = 0;
   out_8169692554973610169[1] = 0;
   out_8169692554973610169[2] = 0;
   out_8169692554973610169[3] = 0;
   out_8169692554973610169[4] = 0;
   out_8169692554973610169[5] = 0;
   out_8169692554973610169[6] = 0;
   out_8169692554973610169[7] = 0;
   out_8169692554973610169[8] = 1;
}
#include <eigen3/Eigen/Dense>
#include <iostream>

typedef Eigen::Matrix<double, DIM, DIM, Eigen::RowMajor> DDM;
typedef Eigen::Matrix<double, EDIM, EDIM, Eigen::RowMajor> EEM;
typedef Eigen::Matrix<double, DIM, EDIM, Eigen::RowMajor> DEM;

void predict(double *in_x, double *in_P, double *in_Q, double dt) {
  typedef Eigen::Matrix<double, MEDIM, MEDIM, Eigen::RowMajor> RRM;

  double nx[DIM] = {0};
  double in_F[EDIM*EDIM] = {0};

  // functions from sympy
  f_fun(in_x, dt, nx);
  F_fun(in_x, dt, in_F);


  EEM F(in_F);
  EEM P(in_P);
  EEM Q(in_Q);

  RRM F_main = F.topLeftCorner(MEDIM, MEDIM);
  P.topLeftCorner(MEDIM, MEDIM) = (F_main * P.topLeftCorner(MEDIM, MEDIM)) * F_main.transpose();
  P.topRightCorner(MEDIM, EDIM - MEDIM) = F_main * P.topRightCorner(MEDIM, EDIM - MEDIM);
  P.bottomLeftCorner(EDIM - MEDIM, MEDIM) = P.bottomLeftCorner(EDIM - MEDIM, MEDIM) * F_main.transpose();

  P = P + dt*Q;

  // copy out state
  memcpy(in_x, nx, DIM * sizeof(double));
  memcpy(in_P, P.data(), EDIM * EDIM * sizeof(double));
}

// note: extra_args dim only correct when null space projecting
// otherwise 1
template <int ZDIM, int EADIM, bool MAHA_TEST>
void update(double *in_x, double *in_P, Hfun h_fun, Hfun H_fun, Hfun Hea_fun, double *in_z, double *in_R, double *in_ea, double MAHA_THRESHOLD) {
  typedef Eigen::Matrix<double, ZDIM, ZDIM, Eigen::RowMajor> ZZM;
  typedef Eigen::Matrix<double, ZDIM, DIM, Eigen::RowMajor> ZDM;
  typedef Eigen::Matrix<double, Eigen::Dynamic, EDIM, Eigen::RowMajor> XEM;
  //typedef Eigen::Matrix<double, EDIM, ZDIM, Eigen::RowMajor> EZM;
  typedef Eigen::Matrix<double, Eigen::Dynamic, 1> X1M;
  typedef Eigen::Matrix<double, Eigen::Dynamic, Eigen::Dynamic, Eigen::RowMajor> XXM;

  double in_hx[ZDIM] = {0};
  double in_H[ZDIM * DIM] = {0};
  double in_H_mod[EDIM * DIM] = {0};
  double delta_x[EDIM] = {0};
  double x_new[DIM] = {0};


  // state x, P
  Eigen::Matrix<double, ZDIM, 1> z(in_z);
  EEM P(in_P);
  ZZM pre_R(in_R);

  // functions from sympy
  h_fun(in_x, in_ea, in_hx);
  H_fun(in_x, in_ea, in_H);
  ZDM pre_H(in_H);

  // get y (y = z - hx)
  Eigen::Matrix<double, ZDIM, 1> pre_y(in_hx); pre_y = z - pre_y;
  X1M y; XXM H; XXM R;
  if (Hea_fun){
    typedef Eigen::Matrix<double, ZDIM, EADIM, Eigen::RowMajor> ZAM;
    double in_Hea[ZDIM * EADIM] = {0};
    Hea_fun(in_x, in_ea, in_Hea);
    ZAM Hea(in_Hea);
    XXM A = Hea.transpose().fullPivLu().kernel();


    y = A.transpose() * pre_y;
    H = A.transpose() * pre_H;
    R = A.transpose() * pre_R * A;
  } else {
    y = pre_y;
    H = pre_H;
    R = pre_R;
  }
  // get modified H
  H_mod_fun(in_x, in_H_mod);
  DEM H_mod(in_H_mod);
  XEM H_err = H * H_mod;

  // Do mahalobis distance test
  if (MAHA_TEST){
    XXM a = (H_err * P * H_err.transpose() + R).inverse();
    double maha_dist = y.transpose() * a * y;
    if (maha_dist > MAHA_THRESHOLD){
      R = 1.0e16 * R;
    }
  }

  // Outlier resilient weighting
  double weight = 1;//(1.5)/(1 + y.squaredNorm()/R.sum());

  // kalman gains and I_KH
  XXM S = ((H_err * P) * H_err.transpose()) + R/weight;
  XEM KT = S.fullPivLu().solve(H_err * P.transpose());
  //EZM K = KT.transpose(); TODO: WHY DOES THIS NOT COMPILE?
  //EZM K = S.fullPivLu().solve(H_err * P.transpose()).transpose();
  //std::cout << "Here is the matrix rot:\n" << K << std::endl;
  EEM I_KH = Eigen::Matrix<double, EDIM, EDIM>::Identity() - (KT.transpose() * H_err);

  // update state by injecting dx
  Eigen::Matrix<double, EDIM, 1> dx(delta_x);
  dx  = (KT.transpose() * y);
  memcpy(delta_x, dx.data(), EDIM * sizeof(double));
  err_fun(in_x, delta_x, x_new);
  Eigen::Matrix<double, DIM, 1> x(x_new);

  // update cov
  P = ((I_KH * P) * I_KH.transpose()) + ((KT.transpose() * R) * KT);

  // copy out state
  memcpy(in_x, x.data(), DIM * sizeof(double));
  memcpy(in_P, P.data(), EDIM * EDIM * sizeof(double));
  memcpy(in_z, y.data(), y.rows() * sizeof(double));
}




}
extern "C" {

void car_update_25(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_25, H_25, NULL, in_z, in_R, in_ea, MAHA_THRESH_25);
}
void car_update_24(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<2, 3, 0>(in_x, in_P, h_24, H_24, NULL, in_z, in_R, in_ea, MAHA_THRESH_24);
}
void car_update_30(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_30, H_30, NULL, in_z, in_R, in_ea, MAHA_THRESH_30);
}
void car_update_26(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_26, H_26, NULL, in_z, in_R, in_ea, MAHA_THRESH_26);
}
void car_update_27(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_27, H_27, NULL, in_z, in_R, in_ea, MAHA_THRESH_27);
}
void car_update_29(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_29, H_29, NULL, in_z, in_R, in_ea, MAHA_THRESH_29);
}
void car_update_28(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_28, H_28, NULL, in_z, in_R, in_ea, MAHA_THRESH_28);
}
void car_update_31(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<1, 3, 0>(in_x, in_P, h_31, H_31, NULL, in_z, in_R, in_ea, MAHA_THRESH_31);
}
void car_err_fun(double *nom_x, double *delta_x, double *out_1014334527327209848) {
  err_fun(nom_x, delta_x, out_1014334527327209848);
}
void car_inv_err_fun(double *nom_x, double *true_x, double *out_3206419511188405682) {
  inv_err_fun(nom_x, true_x, out_3206419511188405682);
}
void car_H_mod_fun(double *state, double *out_992849709061666640) {
  H_mod_fun(state, out_992849709061666640);
}
void car_f_fun(double *state, double dt, double *out_7284810420003339157) {
  f_fun(state,  dt, out_7284810420003339157);
}
void car_F_fun(double *state, double dt, double *out_8279640069865100833) {
  F_fun(state,  dt, out_8279640069865100833);
}
void car_h_25(double *state, double *unused, double *out_8642135170742394965) {
  h_25(state, unused, out_8642135170742394965);
}
void car_H_25(double *state, double *unused, double *out_8200338516850570597) {
  H_25(state, unused, out_8200338516850570597);
}
void car_h_24(double *state, double *unused, double *out_9197263242363141624) {
  h_24(state, unused, out_9197263242363141624);
}
void car_H_24(double *state, double *unused, double *out_1027726669218624628) {
  H_24(state, unused, out_1027726669218624628);
}
void car_h_30(double *state, double *unused, double *out_8720849294696219296) {
  h_30(state, unused, out_8720849294696219296);
}
void car_H_30(double *state, double *unused, double *out_5718709226731372821) {
  H_30(state, unused, out_5718709226731372821);
}
void car_h_26(double *state, double *unused, double *out_4724221511352486675) {
  h_26(state, unused, out_4724221511352486675);
}
void car_H_26(double *state, double *unused, double *out_6504902237984924795) {
  H_26(state, unused, out_6504902237984924795);
}
void car_h_27(double *state, double *unused, double *out_2893523921247530795) {
  h_27(state, unused, out_2893523921247530795);
}
void car_H_27(double *state, double *unused, double *out_7942303297915316038) {
  H_27(state, unused, out_7942303297915316038);
}
void car_h_29(double *state, double *unused, double *out_2618329858963024906) {
  h_29(state, unused, out_2618329858963024906);
}
void car_H_29(double *state, double *unused, double *out_6228940571045765005) {
  H_29(state, unused, out_6228940571045765005);
}
void car_h_28(double *state, double *unused, double *out_8550457448668955042) {
  h_28(state, unused, out_8550457448668955042);
}
void car_H_28(double *state, double *unused, double *out_1146541553976234431) {
  H_28(state, unused, out_1146541553976234431);
}
void car_h_31(double *state, double *unused, double *out_7603820894445690215) {
  h_31(state, unused, out_7603820894445690215);
}
void car_H_31(double *state, double *unused, double *out_8169692554973610169) {
  H_31(state, unused, out_8169692554973610169);
}
void car_predict(double *in_x, double *in_P, double *in_Q, double dt) {
  predict(in_x, in_P, in_Q, dt);
}
void car_set_mass(double x) {
  set_mass(x);
}
void car_set_rotational_inertia(double x) {
  set_rotational_inertia(x);
}
void car_set_center_to_front(double x) {
  set_center_to_front(x);
}
void car_set_center_to_rear(double x) {
  set_center_to_rear(x);
}
void car_set_stiffness_front(double x) {
  set_stiffness_front(x);
}
void car_set_stiffness_rear(double x) {
  set_stiffness_rear(x);
}
}

const EKF car = {
  .name = "car",
  .kinds = { 25, 24, 30, 26, 27, 29, 28, 31 },
  .feature_kinds = {  },
  .f_fun = car_f_fun,
  .F_fun = car_F_fun,
  .err_fun = car_err_fun,
  .inv_err_fun = car_inv_err_fun,
  .H_mod_fun = car_H_mod_fun,
  .predict = car_predict,
  .hs = {
    { 25, car_h_25 },
    { 24, car_h_24 },
    { 30, car_h_30 },
    { 26, car_h_26 },
    { 27, car_h_27 },
    { 29, car_h_29 },
    { 28, car_h_28 },
    { 31, car_h_31 },
  },
  .Hs = {
    { 25, car_H_25 },
    { 24, car_H_24 },
    { 30, car_H_30 },
    { 26, car_H_26 },
    { 27, car_H_27 },
    { 29, car_H_29 },
    { 28, car_H_28 },
    { 31, car_H_31 },
  },
  .updates = {
    { 25, car_update_25 },
    { 24, car_update_24 },
    { 30, car_update_30 },
    { 26, car_update_26 },
    { 27, car_update_27 },
    { 29, car_update_29 },
    { 28, car_update_28 },
    { 31, car_update_31 },
  },
  .Hes = {
  },
  .sets = {
    { "mass", car_set_mass },
    { "rotational_inertia", car_set_rotational_inertia },
    { "center_to_front", car_set_center_to_front },
    { "center_to_rear", car_set_center_to_rear },
    { "stiffness_front", car_set_stiffness_front },
    { "stiffness_rear", car_set_stiffness_rear },
  },
  .extra_routines = {
  },
};

ekf_lib_init(car)
