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
void err_fun(double *nom_x, double *delta_x, double *out_6317102778004151788) {
   out_6317102778004151788[0] = delta_x[0] + nom_x[0];
   out_6317102778004151788[1] = delta_x[1] + nom_x[1];
   out_6317102778004151788[2] = delta_x[2] + nom_x[2];
   out_6317102778004151788[3] = delta_x[3] + nom_x[3];
   out_6317102778004151788[4] = delta_x[4] + nom_x[4];
   out_6317102778004151788[5] = delta_x[5] + nom_x[5];
   out_6317102778004151788[6] = delta_x[6] + nom_x[6];
   out_6317102778004151788[7] = delta_x[7] + nom_x[7];
   out_6317102778004151788[8] = delta_x[8] + nom_x[8];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_7905438631293668286) {
   out_7905438631293668286[0] = -nom_x[0] + true_x[0];
   out_7905438631293668286[1] = -nom_x[1] + true_x[1];
   out_7905438631293668286[2] = -nom_x[2] + true_x[2];
   out_7905438631293668286[3] = -nom_x[3] + true_x[3];
   out_7905438631293668286[4] = -nom_x[4] + true_x[4];
   out_7905438631293668286[5] = -nom_x[5] + true_x[5];
   out_7905438631293668286[6] = -nom_x[6] + true_x[6];
   out_7905438631293668286[7] = -nom_x[7] + true_x[7];
   out_7905438631293668286[8] = -nom_x[8] + true_x[8];
}
void H_mod_fun(double *state, double *out_506592663844380792) {
   out_506592663844380792[0] = 1.0;
   out_506592663844380792[1] = 0.0;
   out_506592663844380792[2] = 0.0;
   out_506592663844380792[3] = 0.0;
   out_506592663844380792[4] = 0.0;
   out_506592663844380792[5] = 0.0;
   out_506592663844380792[6] = 0.0;
   out_506592663844380792[7] = 0.0;
   out_506592663844380792[8] = 0.0;
   out_506592663844380792[9] = 0.0;
   out_506592663844380792[10] = 1.0;
   out_506592663844380792[11] = 0.0;
   out_506592663844380792[12] = 0.0;
   out_506592663844380792[13] = 0.0;
   out_506592663844380792[14] = 0.0;
   out_506592663844380792[15] = 0.0;
   out_506592663844380792[16] = 0.0;
   out_506592663844380792[17] = 0.0;
   out_506592663844380792[18] = 0.0;
   out_506592663844380792[19] = 0.0;
   out_506592663844380792[20] = 1.0;
   out_506592663844380792[21] = 0.0;
   out_506592663844380792[22] = 0.0;
   out_506592663844380792[23] = 0.0;
   out_506592663844380792[24] = 0.0;
   out_506592663844380792[25] = 0.0;
   out_506592663844380792[26] = 0.0;
   out_506592663844380792[27] = 0.0;
   out_506592663844380792[28] = 0.0;
   out_506592663844380792[29] = 0.0;
   out_506592663844380792[30] = 1.0;
   out_506592663844380792[31] = 0.0;
   out_506592663844380792[32] = 0.0;
   out_506592663844380792[33] = 0.0;
   out_506592663844380792[34] = 0.0;
   out_506592663844380792[35] = 0.0;
   out_506592663844380792[36] = 0.0;
   out_506592663844380792[37] = 0.0;
   out_506592663844380792[38] = 0.0;
   out_506592663844380792[39] = 0.0;
   out_506592663844380792[40] = 1.0;
   out_506592663844380792[41] = 0.0;
   out_506592663844380792[42] = 0.0;
   out_506592663844380792[43] = 0.0;
   out_506592663844380792[44] = 0.0;
   out_506592663844380792[45] = 0.0;
   out_506592663844380792[46] = 0.0;
   out_506592663844380792[47] = 0.0;
   out_506592663844380792[48] = 0.0;
   out_506592663844380792[49] = 0.0;
   out_506592663844380792[50] = 1.0;
   out_506592663844380792[51] = 0.0;
   out_506592663844380792[52] = 0.0;
   out_506592663844380792[53] = 0.0;
   out_506592663844380792[54] = 0.0;
   out_506592663844380792[55] = 0.0;
   out_506592663844380792[56] = 0.0;
   out_506592663844380792[57] = 0.0;
   out_506592663844380792[58] = 0.0;
   out_506592663844380792[59] = 0.0;
   out_506592663844380792[60] = 1.0;
   out_506592663844380792[61] = 0.0;
   out_506592663844380792[62] = 0.0;
   out_506592663844380792[63] = 0.0;
   out_506592663844380792[64] = 0.0;
   out_506592663844380792[65] = 0.0;
   out_506592663844380792[66] = 0.0;
   out_506592663844380792[67] = 0.0;
   out_506592663844380792[68] = 0.0;
   out_506592663844380792[69] = 0.0;
   out_506592663844380792[70] = 1.0;
   out_506592663844380792[71] = 0.0;
   out_506592663844380792[72] = 0.0;
   out_506592663844380792[73] = 0.0;
   out_506592663844380792[74] = 0.0;
   out_506592663844380792[75] = 0.0;
   out_506592663844380792[76] = 0.0;
   out_506592663844380792[77] = 0.0;
   out_506592663844380792[78] = 0.0;
   out_506592663844380792[79] = 0.0;
   out_506592663844380792[80] = 1.0;
}
void f_fun(double *state, double dt, double *out_2942986894476270401) {
   out_2942986894476270401[0] = state[0];
   out_2942986894476270401[1] = state[1];
   out_2942986894476270401[2] = state[2];
   out_2942986894476270401[3] = state[3];
   out_2942986894476270401[4] = state[4];
   out_2942986894476270401[5] = dt*((-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]))*state[6] - 9.8100000000000005*state[8] + stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*state[1]) + (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*state[4])) + state[5];
   out_2942986894476270401[6] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*state[4])) + state[6];
   out_2942986894476270401[7] = state[7];
   out_2942986894476270401[8] = state[8];
}
void F_fun(double *state, double dt, double *out_1935296345496298881) {
   out_1935296345496298881[0] = 1;
   out_1935296345496298881[1] = 0;
   out_1935296345496298881[2] = 0;
   out_1935296345496298881[3] = 0;
   out_1935296345496298881[4] = 0;
   out_1935296345496298881[5] = 0;
   out_1935296345496298881[6] = 0;
   out_1935296345496298881[7] = 0;
   out_1935296345496298881[8] = 0;
   out_1935296345496298881[9] = 0;
   out_1935296345496298881[10] = 1;
   out_1935296345496298881[11] = 0;
   out_1935296345496298881[12] = 0;
   out_1935296345496298881[13] = 0;
   out_1935296345496298881[14] = 0;
   out_1935296345496298881[15] = 0;
   out_1935296345496298881[16] = 0;
   out_1935296345496298881[17] = 0;
   out_1935296345496298881[18] = 0;
   out_1935296345496298881[19] = 0;
   out_1935296345496298881[20] = 1;
   out_1935296345496298881[21] = 0;
   out_1935296345496298881[22] = 0;
   out_1935296345496298881[23] = 0;
   out_1935296345496298881[24] = 0;
   out_1935296345496298881[25] = 0;
   out_1935296345496298881[26] = 0;
   out_1935296345496298881[27] = 0;
   out_1935296345496298881[28] = 0;
   out_1935296345496298881[29] = 0;
   out_1935296345496298881[30] = 1;
   out_1935296345496298881[31] = 0;
   out_1935296345496298881[32] = 0;
   out_1935296345496298881[33] = 0;
   out_1935296345496298881[34] = 0;
   out_1935296345496298881[35] = 0;
   out_1935296345496298881[36] = 0;
   out_1935296345496298881[37] = 0;
   out_1935296345496298881[38] = 0;
   out_1935296345496298881[39] = 0;
   out_1935296345496298881[40] = 1;
   out_1935296345496298881[41] = 0;
   out_1935296345496298881[42] = 0;
   out_1935296345496298881[43] = 0;
   out_1935296345496298881[44] = 0;
   out_1935296345496298881[45] = dt*(stiffness_front*(-state[2] - state[3] + state[7])/(mass*state[1]) + (-stiffness_front - stiffness_rear)*state[5]/(mass*state[4]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[6]/(mass*state[4]));
   out_1935296345496298881[46] = -dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*pow(state[1], 2));
   out_1935296345496298881[47] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_1935296345496298881[48] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_1935296345496298881[49] = dt*((-1 - (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*pow(state[4], 2)))*state[6] - (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*pow(state[4], 2)));
   out_1935296345496298881[50] = dt*(-stiffness_front*state[0] - stiffness_rear*state[0])/(mass*state[4]) + 1;
   out_1935296345496298881[51] = dt*(-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]));
   out_1935296345496298881[52] = dt*stiffness_front*state[0]/(mass*state[1]);
   out_1935296345496298881[53] = -9.8100000000000005*dt;
   out_1935296345496298881[54] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front - pow(center_to_rear, 2)*stiffness_rear)*state[6]/(rotational_inertia*state[4]));
   out_1935296345496298881[55] = -center_to_front*dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*pow(state[1], 2));
   out_1935296345496298881[56] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_1935296345496298881[57] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_1935296345496298881[58] = dt*(-(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*pow(state[4], 2)) - (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*pow(state[4], 2)));
   out_1935296345496298881[59] = dt*(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(rotational_inertia*state[4]);
   out_1935296345496298881[60] = dt*(-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])/(rotational_inertia*state[4]) + 1;
   out_1935296345496298881[61] = center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_1935296345496298881[62] = 0;
   out_1935296345496298881[63] = 0;
   out_1935296345496298881[64] = 0;
   out_1935296345496298881[65] = 0;
   out_1935296345496298881[66] = 0;
   out_1935296345496298881[67] = 0;
   out_1935296345496298881[68] = 0;
   out_1935296345496298881[69] = 0;
   out_1935296345496298881[70] = 1;
   out_1935296345496298881[71] = 0;
   out_1935296345496298881[72] = 0;
   out_1935296345496298881[73] = 0;
   out_1935296345496298881[74] = 0;
   out_1935296345496298881[75] = 0;
   out_1935296345496298881[76] = 0;
   out_1935296345496298881[77] = 0;
   out_1935296345496298881[78] = 0;
   out_1935296345496298881[79] = 0;
   out_1935296345496298881[80] = 1;
}
void h_25(double *state, double *unused, double *out_3724255488027275150) {
   out_3724255488027275150[0] = state[6];
}
void H_25(double *state, double *unused, double *out_8257565196214887805) {
   out_8257565196214887805[0] = 0;
   out_8257565196214887805[1] = 0;
   out_8257565196214887805[2] = 0;
   out_8257565196214887805[3] = 0;
   out_8257565196214887805[4] = 0;
   out_8257565196214887805[5] = 0;
   out_8257565196214887805[6] = 1;
   out_8257565196214887805[7] = 0;
   out_8257565196214887805[8] = 0;
}
void h_24(double *state, double *unused, double *out_5876561176471029432) {
   out_5876561176471029432[0] = state[4];
   out_5876561176471029432[1] = state[5];
}
void H_24(double *state, double *unused, double *out_8776150629456254974) {
   out_8776150629456254974[0] = 0;
   out_8776150629456254974[1] = 0;
   out_8776150629456254974[2] = 0;
   out_8776150629456254974[3] = 0;
   out_8776150629456254974[4] = 1;
   out_8776150629456254974[5] = 0;
   out_8776150629456254974[6] = 0;
   out_8776150629456254974[7] = 0;
   out_8776150629456254974[8] = 0;
   out_8776150629456254974[9] = 0;
   out_8776150629456254974[10] = 0;
   out_8776150629456254974[11] = 0;
   out_8776150629456254974[12] = 0;
   out_8776150629456254974[13] = 0;
   out_8776150629456254974[14] = 1;
   out_8776150629456254974[15] = 0;
   out_8776150629456254974[16] = 0;
   out_8776150629456254974[17] = 0;
}
void h_30(double *state, double *unused, double *out_4915621988691266734) {
   out_4915621988691266734[0] = state[4];
}
void H_30(double *state, double *unused, double *out_7670845918987415184) {
   out_7670845918987415184[0] = 0;
   out_7670845918987415184[1] = 0;
   out_7670845918987415184[2] = 0;
   out_7670845918987415184[3] = 0;
   out_7670845918987415184[4] = 1;
   out_7670845918987415184[5] = 0;
   out_7670845918987415184[6] = 0;
   out_7670845918987415184[7] = 0;
   out_7670845918987415184[8] = 0;
}
void h_26(double *state, double *unused, double *out_8529448817357439237) {
   out_8529448817357439237[0] = state[7];
}
void H_26(double *state, double *unused, double *out_4516061877340831581) {
   out_4516061877340831581[0] = 0;
   out_4516061877340831581[1] = 0;
   out_4516061877340831581[2] = 0;
   out_4516061877340831581[3] = 0;
   out_4516061877340831581[4] = 0;
   out_4516061877340831581[5] = 0;
   out_4516061877340831581[6] = 0;
   out_4516061877340831581[7] = 1;
   out_4516061877340831581[8] = 0;
}
void h_27(double *state, double *unused, double *out_333074405041906056) {
   out_333074405041906056[0] = state[3];
}
void H_27(double *state, double *unused, double *out_5447251847803471967) {
   out_5447251847803471967[0] = 0;
   out_5447251847803471967[1] = 0;
   out_5447251847803471967[2] = 0;
   out_5447251847803471967[3] = 1;
   out_5447251847803471967[4] = 0;
   out_5447251847803471967[5] = 0;
   out_5447251847803471967[6] = 0;
   out_5447251847803471967[7] = 0;
   out_5447251847803471967[8] = 0;
}
void h_29(double *state, double *unused, double *out_1186897447041927656) {
   out_1186897447041927656[0] = state[1];
}
void H_29(double *state, double *unused, double *out_7160614574673023000) {
   out_7160614574673023000[0] = 0;
   out_7160614574673023000[1] = 1;
   out_7160614574673023000[2] = 0;
   out_7160614574673023000[3] = 0;
   out_7160614574673023000[4] = 0;
   out_7160614574673023000[5] = 0;
   out_7160614574673023000[6] = 0;
   out_7160614574673023000[7] = 0;
   out_7160614574673023000[8] = 0;
}
void h_28(double *state, double *unused, double *out_6091059319035644012) {
   out_6091059319035644012[0] = state[0];
}
void H_28(double *state, double *unused, double *out_6203730481966998042) {
   out_6203730481966998042[0] = 1;
   out_6203730481966998042[1] = 0;
   out_6203730481966998042[2] = 0;
   out_6203730481966998042[3] = 0;
   out_6203730481966998042[4] = 0;
   out_6203730481966998042[5] = 0;
   out_6203730481966998042[6] = 0;
   out_6203730481966998042[7] = 0;
   out_6203730481966998042[8] = 0;
}
void h_31(double *state, double *unused, double *out_3884116280073868534) {
   out_3884116280073868534[0] = state[8];
}
void H_31(double *state, double *unused, double *out_8288211158091848233) {
   out_8288211158091848233[0] = 0;
   out_8288211158091848233[1] = 0;
   out_8288211158091848233[2] = 0;
   out_8288211158091848233[3] = 0;
   out_8288211158091848233[4] = 0;
   out_8288211158091848233[5] = 0;
   out_8288211158091848233[6] = 0;
   out_8288211158091848233[7] = 0;
   out_8288211158091848233[8] = 1;
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
void car_err_fun(double *nom_x, double *delta_x, double *out_6317102778004151788) {
  err_fun(nom_x, delta_x, out_6317102778004151788);
}
void car_inv_err_fun(double *nom_x, double *true_x, double *out_7905438631293668286) {
  inv_err_fun(nom_x, true_x, out_7905438631293668286);
}
void car_H_mod_fun(double *state, double *out_506592663844380792) {
  H_mod_fun(state, out_506592663844380792);
}
void car_f_fun(double *state, double dt, double *out_2942986894476270401) {
  f_fun(state,  dt, out_2942986894476270401);
}
void car_F_fun(double *state, double dt, double *out_1935296345496298881) {
  F_fun(state,  dt, out_1935296345496298881);
}
void car_h_25(double *state, double *unused, double *out_3724255488027275150) {
  h_25(state, unused, out_3724255488027275150);
}
void car_H_25(double *state, double *unused, double *out_8257565196214887805) {
  H_25(state, unused, out_8257565196214887805);
}
void car_h_24(double *state, double *unused, double *out_5876561176471029432) {
  h_24(state, unused, out_5876561176471029432);
}
void car_H_24(double *state, double *unused, double *out_8776150629456254974) {
  H_24(state, unused, out_8776150629456254974);
}
void car_h_30(double *state, double *unused, double *out_4915621988691266734) {
  h_30(state, unused, out_4915621988691266734);
}
void car_H_30(double *state, double *unused, double *out_7670845918987415184) {
  H_30(state, unused, out_7670845918987415184);
}
void car_h_26(double *state, double *unused, double *out_8529448817357439237) {
  h_26(state, unused, out_8529448817357439237);
}
void car_H_26(double *state, double *unused, double *out_4516061877340831581) {
  H_26(state, unused, out_4516061877340831581);
}
void car_h_27(double *state, double *unused, double *out_333074405041906056) {
  h_27(state, unused, out_333074405041906056);
}
void car_H_27(double *state, double *unused, double *out_5447251847803471967) {
  H_27(state, unused, out_5447251847803471967);
}
void car_h_29(double *state, double *unused, double *out_1186897447041927656) {
  h_29(state, unused, out_1186897447041927656);
}
void car_H_29(double *state, double *unused, double *out_7160614574673023000) {
  H_29(state, unused, out_7160614574673023000);
}
void car_h_28(double *state, double *unused, double *out_6091059319035644012) {
  h_28(state, unused, out_6091059319035644012);
}
void car_H_28(double *state, double *unused, double *out_6203730481966998042) {
  H_28(state, unused, out_6203730481966998042);
}
void car_h_31(double *state, double *unused, double *out_3884116280073868534) {
  h_31(state, unused, out_3884116280073868534);
}
void car_H_31(double *state, double *unused, double *out_8288211158091848233) {
  H_31(state, unused, out_8288211158091848233);
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
