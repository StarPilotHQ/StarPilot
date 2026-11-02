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
void err_fun(double *nom_x, double *delta_x, double *out_2869805078350318634) {
   out_2869805078350318634[0] = delta_x[0] + nom_x[0];
   out_2869805078350318634[1] = delta_x[1] + nom_x[1];
   out_2869805078350318634[2] = delta_x[2] + nom_x[2];
   out_2869805078350318634[3] = delta_x[3] + nom_x[3];
   out_2869805078350318634[4] = delta_x[4] + nom_x[4];
   out_2869805078350318634[5] = delta_x[5] + nom_x[5];
   out_2869805078350318634[6] = delta_x[6] + nom_x[6];
   out_2869805078350318634[7] = delta_x[7] + nom_x[7];
   out_2869805078350318634[8] = delta_x[8] + nom_x[8];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_6624138795093603288) {
   out_6624138795093603288[0] = -nom_x[0] + true_x[0];
   out_6624138795093603288[1] = -nom_x[1] + true_x[1];
   out_6624138795093603288[2] = -nom_x[2] + true_x[2];
   out_6624138795093603288[3] = -nom_x[3] + true_x[3];
   out_6624138795093603288[4] = -nom_x[4] + true_x[4];
   out_6624138795093603288[5] = -nom_x[5] + true_x[5];
   out_6624138795093603288[6] = -nom_x[6] + true_x[6];
   out_6624138795093603288[7] = -nom_x[7] + true_x[7];
   out_6624138795093603288[8] = -nom_x[8] + true_x[8];
}
void H_mod_fun(double *state, double *out_6424885359819808556) {
   out_6424885359819808556[0] = 1.0;
   out_6424885359819808556[1] = 0.0;
   out_6424885359819808556[2] = 0.0;
   out_6424885359819808556[3] = 0.0;
   out_6424885359819808556[4] = 0.0;
   out_6424885359819808556[5] = 0.0;
   out_6424885359819808556[6] = 0.0;
   out_6424885359819808556[7] = 0.0;
   out_6424885359819808556[8] = 0.0;
   out_6424885359819808556[9] = 0.0;
   out_6424885359819808556[10] = 1.0;
   out_6424885359819808556[11] = 0.0;
   out_6424885359819808556[12] = 0.0;
   out_6424885359819808556[13] = 0.0;
   out_6424885359819808556[14] = 0.0;
   out_6424885359819808556[15] = 0.0;
   out_6424885359819808556[16] = 0.0;
   out_6424885359819808556[17] = 0.0;
   out_6424885359819808556[18] = 0.0;
   out_6424885359819808556[19] = 0.0;
   out_6424885359819808556[20] = 1.0;
   out_6424885359819808556[21] = 0.0;
   out_6424885359819808556[22] = 0.0;
   out_6424885359819808556[23] = 0.0;
   out_6424885359819808556[24] = 0.0;
   out_6424885359819808556[25] = 0.0;
   out_6424885359819808556[26] = 0.0;
   out_6424885359819808556[27] = 0.0;
   out_6424885359819808556[28] = 0.0;
   out_6424885359819808556[29] = 0.0;
   out_6424885359819808556[30] = 1.0;
   out_6424885359819808556[31] = 0.0;
   out_6424885359819808556[32] = 0.0;
   out_6424885359819808556[33] = 0.0;
   out_6424885359819808556[34] = 0.0;
   out_6424885359819808556[35] = 0.0;
   out_6424885359819808556[36] = 0.0;
   out_6424885359819808556[37] = 0.0;
   out_6424885359819808556[38] = 0.0;
   out_6424885359819808556[39] = 0.0;
   out_6424885359819808556[40] = 1.0;
   out_6424885359819808556[41] = 0.0;
   out_6424885359819808556[42] = 0.0;
   out_6424885359819808556[43] = 0.0;
   out_6424885359819808556[44] = 0.0;
   out_6424885359819808556[45] = 0.0;
   out_6424885359819808556[46] = 0.0;
   out_6424885359819808556[47] = 0.0;
   out_6424885359819808556[48] = 0.0;
   out_6424885359819808556[49] = 0.0;
   out_6424885359819808556[50] = 1.0;
   out_6424885359819808556[51] = 0.0;
   out_6424885359819808556[52] = 0.0;
   out_6424885359819808556[53] = 0.0;
   out_6424885359819808556[54] = 0.0;
   out_6424885359819808556[55] = 0.0;
   out_6424885359819808556[56] = 0.0;
   out_6424885359819808556[57] = 0.0;
   out_6424885359819808556[58] = 0.0;
   out_6424885359819808556[59] = 0.0;
   out_6424885359819808556[60] = 1.0;
   out_6424885359819808556[61] = 0.0;
   out_6424885359819808556[62] = 0.0;
   out_6424885359819808556[63] = 0.0;
   out_6424885359819808556[64] = 0.0;
   out_6424885359819808556[65] = 0.0;
   out_6424885359819808556[66] = 0.0;
   out_6424885359819808556[67] = 0.0;
   out_6424885359819808556[68] = 0.0;
   out_6424885359819808556[69] = 0.0;
   out_6424885359819808556[70] = 1.0;
   out_6424885359819808556[71] = 0.0;
   out_6424885359819808556[72] = 0.0;
   out_6424885359819808556[73] = 0.0;
   out_6424885359819808556[74] = 0.0;
   out_6424885359819808556[75] = 0.0;
   out_6424885359819808556[76] = 0.0;
   out_6424885359819808556[77] = 0.0;
   out_6424885359819808556[78] = 0.0;
   out_6424885359819808556[79] = 0.0;
   out_6424885359819808556[80] = 1.0;
}
void f_fun(double *state, double dt, double *out_3758174016504973643) {
   out_3758174016504973643[0] = state[0];
   out_3758174016504973643[1] = state[1];
   out_3758174016504973643[2] = state[2];
   out_3758174016504973643[3] = state[3];
   out_3758174016504973643[4] = state[4];
   out_3758174016504973643[5] = dt*((-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]))*state[6] - 9.8100000000000005*state[8] + stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*state[1]) + (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*state[4])) + state[5];
   out_3758174016504973643[6] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*state[4])) + state[6];
   out_3758174016504973643[7] = state[7];
   out_3758174016504973643[8] = state[8];
}
void F_fun(double *state, double dt, double *out_6192627187967339842) {
   out_6192627187967339842[0] = 1;
   out_6192627187967339842[1] = 0;
   out_6192627187967339842[2] = 0;
   out_6192627187967339842[3] = 0;
   out_6192627187967339842[4] = 0;
   out_6192627187967339842[5] = 0;
   out_6192627187967339842[6] = 0;
   out_6192627187967339842[7] = 0;
   out_6192627187967339842[8] = 0;
   out_6192627187967339842[9] = 0;
   out_6192627187967339842[10] = 1;
   out_6192627187967339842[11] = 0;
   out_6192627187967339842[12] = 0;
   out_6192627187967339842[13] = 0;
   out_6192627187967339842[14] = 0;
   out_6192627187967339842[15] = 0;
   out_6192627187967339842[16] = 0;
   out_6192627187967339842[17] = 0;
   out_6192627187967339842[18] = 0;
   out_6192627187967339842[19] = 0;
   out_6192627187967339842[20] = 1;
   out_6192627187967339842[21] = 0;
   out_6192627187967339842[22] = 0;
   out_6192627187967339842[23] = 0;
   out_6192627187967339842[24] = 0;
   out_6192627187967339842[25] = 0;
   out_6192627187967339842[26] = 0;
   out_6192627187967339842[27] = 0;
   out_6192627187967339842[28] = 0;
   out_6192627187967339842[29] = 0;
   out_6192627187967339842[30] = 1;
   out_6192627187967339842[31] = 0;
   out_6192627187967339842[32] = 0;
   out_6192627187967339842[33] = 0;
   out_6192627187967339842[34] = 0;
   out_6192627187967339842[35] = 0;
   out_6192627187967339842[36] = 0;
   out_6192627187967339842[37] = 0;
   out_6192627187967339842[38] = 0;
   out_6192627187967339842[39] = 0;
   out_6192627187967339842[40] = 1;
   out_6192627187967339842[41] = 0;
   out_6192627187967339842[42] = 0;
   out_6192627187967339842[43] = 0;
   out_6192627187967339842[44] = 0;
   out_6192627187967339842[45] = dt*(stiffness_front*(-state[2] - state[3] + state[7])/(mass*state[1]) + (-stiffness_front - stiffness_rear)*state[5]/(mass*state[4]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[6]/(mass*state[4]));
   out_6192627187967339842[46] = -dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*pow(state[1], 2));
   out_6192627187967339842[47] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_6192627187967339842[48] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_6192627187967339842[49] = dt*((-1 - (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*pow(state[4], 2)))*state[6] - (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*pow(state[4], 2)));
   out_6192627187967339842[50] = dt*(-stiffness_front*state[0] - stiffness_rear*state[0])/(mass*state[4]) + 1;
   out_6192627187967339842[51] = dt*(-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]));
   out_6192627187967339842[52] = dt*stiffness_front*state[0]/(mass*state[1]);
   out_6192627187967339842[53] = -9.8100000000000005*dt;
   out_6192627187967339842[54] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front - pow(center_to_rear, 2)*stiffness_rear)*state[6]/(rotational_inertia*state[4]));
   out_6192627187967339842[55] = -center_to_front*dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*pow(state[1], 2));
   out_6192627187967339842[56] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_6192627187967339842[57] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_6192627187967339842[58] = dt*(-(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*pow(state[4], 2)) - (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*pow(state[4], 2)));
   out_6192627187967339842[59] = dt*(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(rotational_inertia*state[4]);
   out_6192627187967339842[60] = dt*(-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])/(rotational_inertia*state[4]) + 1;
   out_6192627187967339842[61] = center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_6192627187967339842[62] = 0;
   out_6192627187967339842[63] = 0;
   out_6192627187967339842[64] = 0;
   out_6192627187967339842[65] = 0;
   out_6192627187967339842[66] = 0;
   out_6192627187967339842[67] = 0;
   out_6192627187967339842[68] = 0;
   out_6192627187967339842[69] = 0;
   out_6192627187967339842[70] = 1;
   out_6192627187967339842[71] = 0;
   out_6192627187967339842[72] = 0;
   out_6192627187967339842[73] = 0;
   out_6192627187967339842[74] = 0;
   out_6192627187967339842[75] = 0;
   out_6192627187967339842[76] = 0;
   out_6192627187967339842[77] = 0;
   out_6192627187967339842[78] = 0;
   out_6192627187967339842[79] = 0;
   out_6192627187967339842[80] = 1;
}
void h_25(double *state, double *unused, double *out_1759823454732362499) {
   out_1759823454732362499[0] = state[6];
}
void H_25(double *state, double *unused, double *out_2901904709830316879) {
   out_2901904709830316879[0] = 0;
   out_2901904709830316879[1] = 0;
   out_2901904709830316879[2] = 0;
   out_2901904709830316879[3] = 0;
   out_2901904709830316879[4] = 0;
   out_2901904709830316879[5] = 0;
   out_2901904709830316879[6] = 1;
   out_2901904709830316879[7] = 0;
   out_2901904709830316879[8] = 0;
}
void h_24(double *state, double *unused, double *out_1305817215653664496) {
   out_1305817215653664496[0] = state[4];
   out_1305817215653664496[1] = state[5];
}
void H_24(double *state, double *unused, double *out_656020888972006257) {
   out_656020888972006257[0] = 0;
   out_656020888972006257[1] = 0;
   out_656020888972006257[2] = 0;
   out_656020888972006257[3] = 0;
   out_656020888972006257[4] = 1;
   out_656020888972006257[5] = 0;
   out_656020888972006257[6] = 0;
   out_656020888972006257[7] = 0;
   out_656020888972006257[8] = 0;
   out_656020888972006257[9] = 0;
   out_656020888972006257[10] = 0;
   out_656020888972006257[11] = 0;
   out_656020888972006257[12] = 0;
   out_656020888972006257[13] = 0;
   out_656020888972006257[14] = 1;
   out_656020888972006257[15] = 0;
   out_656020888972006257[16] = 0;
   out_656020888972006257[17] = 0;
}
void h_30(double *state, double *unused, double *out_9220382218981250824) {
   out_9220382218981250824[0] = state[4];
}
void H_30(double *state, double *unused, double *out_3031243656973556949) {
   out_3031243656973556949[0] = 0;
   out_3031243656973556949[1] = 0;
   out_3031243656973556949[2] = 0;
   out_3031243656973556949[3] = 0;
   out_3031243656973556949[4] = 1;
   out_3031243656973556949[5] = 0;
   out_3031243656973556949[6] = 0;
   out_3031243656973556949[7] = 0;
   out_3031243656973556949[8] = 0;
}
void h_26(double *state, double *unused, double *out_2821340701567401678) {
   out_2821340701567401678[0] = state[7];
}
void H_26(double *state, double *unused, double *out_6643408028704373103) {
   out_6643408028704373103[0] = 0;
   out_6643408028704373103[1] = 0;
   out_6643408028704373103[2] = 0;
   out_6643408028704373103[3] = 0;
   out_6643408028704373103[4] = 0;
   out_6643408028704373103[5] = 0;
   out_6643408028704373103[6] = 0;
   out_6643408028704373103[7] = 1;
   out_6643408028704373103[8] = 0;
}
void h_27(double *state, double *unused, double *out_2703333307634024934) {
   out_2703333307634024934[0] = state[3];
}
void H_27(double *state, double *unused, double *out_5206006968773981860) {
   out_5206006968773981860[0] = 0;
   out_5206006968773981860[1] = 0;
   out_5206006968773981860[2] = 0;
   out_5206006968773981860[3] = 1;
   out_5206006968773981860[4] = 0;
   out_5206006968773981860[5] = 0;
   out_5206006968773981860[6] = 0;
   out_5206006968773981860[7] = 0;
   out_5206006968773981860[8] = 0;
}
void h_29(double *state, double *unused, double *out_7103533289356984798) {
   out_7103533289356984798[0] = state[1];
}
void H_29(double *state, double *unused, double *out_6919369695643532893) {
   out_6919369695643532893[0] = 0;
   out_6919369695643532893[1] = 1;
   out_6919369695643532893[2] = 0;
   out_6919369695643532893[3] = 0;
   out_6919369695643532893[4] = 0;
   out_6919369695643532893[5] = 0;
   out_6919369695643532893[6] = 0;
   out_6919369695643532893[7] = 0;
   out_6919369695643532893[8] = 0;
}
void h_28(double *state, double *unused, double *out_5740119359078357619) {
   out_5740119359078357619[0] = state[0];
}
void H_28(double *state, double *unused, double *out_4955739424078206642) {
   out_4955739424078206642[0] = 1;
   out_4955739424078206642[1] = 0;
   out_4955739424078206642[2] = 0;
   out_4955739424078206642[3] = 0;
   out_4955739424078206642[4] = 0;
   out_4955739424078206642[5] = 0;
   out_4955739424078206642[6] = 0;
   out_4955739424078206642[7] = 0;
   out_4955739424078206642[8] = 0;
}
void h_31(double *state, double *unused, double *out_1602636786381233354) {
   out_1602636786381233354[0] = state[8];
}
void H_31(double *state, double *unused, double *out_2871258747953356451) {
   out_2871258747953356451[0] = 0;
   out_2871258747953356451[1] = 0;
   out_2871258747953356451[2] = 0;
   out_2871258747953356451[3] = 0;
   out_2871258747953356451[4] = 0;
   out_2871258747953356451[5] = 0;
   out_2871258747953356451[6] = 0;
   out_2871258747953356451[7] = 0;
   out_2871258747953356451[8] = 1;
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
void car_err_fun(double *nom_x, double *delta_x, double *out_2869805078350318634) {
  err_fun(nom_x, delta_x, out_2869805078350318634);
}
void car_inv_err_fun(double *nom_x, double *true_x, double *out_6624138795093603288) {
  inv_err_fun(nom_x, true_x, out_6624138795093603288);
}
void car_H_mod_fun(double *state, double *out_6424885359819808556) {
  H_mod_fun(state, out_6424885359819808556);
}
void car_f_fun(double *state, double dt, double *out_3758174016504973643) {
  f_fun(state,  dt, out_3758174016504973643);
}
void car_F_fun(double *state, double dt, double *out_6192627187967339842) {
  F_fun(state,  dt, out_6192627187967339842);
}
void car_h_25(double *state, double *unused, double *out_1759823454732362499) {
  h_25(state, unused, out_1759823454732362499);
}
void car_H_25(double *state, double *unused, double *out_2901904709830316879) {
  H_25(state, unused, out_2901904709830316879);
}
void car_h_24(double *state, double *unused, double *out_1305817215653664496) {
  h_24(state, unused, out_1305817215653664496);
}
void car_H_24(double *state, double *unused, double *out_656020888972006257) {
  H_24(state, unused, out_656020888972006257);
}
void car_h_30(double *state, double *unused, double *out_9220382218981250824) {
  h_30(state, unused, out_9220382218981250824);
}
void car_H_30(double *state, double *unused, double *out_3031243656973556949) {
  H_30(state, unused, out_3031243656973556949);
}
void car_h_26(double *state, double *unused, double *out_2821340701567401678) {
  h_26(state, unused, out_2821340701567401678);
}
void car_H_26(double *state, double *unused, double *out_6643408028704373103) {
  H_26(state, unused, out_6643408028704373103);
}
void car_h_27(double *state, double *unused, double *out_2703333307634024934) {
  h_27(state, unused, out_2703333307634024934);
}
void car_H_27(double *state, double *unused, double *out_5206006968773981860) {
  H_27(state, unused, out_5206006968773981860);
}
void car_h_29(double *state, double *unused, double *out_7103533289356984798) {
  h_29(state, unused, out_7103533289356984798);
}
void car_H_29(double *state, double *unused, double *out_6919369695643532893) {
  H_29(state, unused, out_6919369695643532893);
}
void car_h_28(double *state, double *unused, double *out_5740119359078357619) {
  h_28(state, unused, out_5740119359078357619);
}
void car_H_28(double *state, double *unused, double *out_4955739424078206642) {
  H_28(state, unused, out_4955739424078206642);
}
void car_h_31(double *state, double *unused, double *out_1602636786381233354) {
  h_31(state, unused, out_1602636786381233354);
}
void car_H_31(double *state, double *unused, double *out_2871258747953356451) {
  H_31(state, unused, out_2871258747953356451);
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
