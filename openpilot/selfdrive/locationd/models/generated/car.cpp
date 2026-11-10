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
void err_fun(double *nom_x, double *delta_x, double *out_3392229238668395910) {
   out_3392229238668395910[0] = delta_x[0] + nom_x[0];
   out_3392229238668395910[1] = delta_x[1] + nom_x[1];
   out_3392229238668395910[2] = delta_x[2] + nom_x[2];
   out_3392229238668395910[3] = delta_x[3] + nom_x[3];
   out_3392229238668395910[4] = delta_x[4] + nom_x[4];
   out_3392229238668395910[5] = delta_x[5] + nom_x[5];
   out_3392229238668395910[6] = delta_x[6] + nom_x[6];
   out_3392229238668395910[7] = delta_x[7] + nom_x[7];
   out_3392229238668395910[8] = delta_x[8] + nom_x[8];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_8775703397352493346) {
   out_8775703397352493346[0] = -nom_x[0] + true_x[0];
   out_8775703397352493346[1] = -nom_x[1] + true_x[1];
   out_8775703397352493346[2] = -nom_x[2] + true_x[2];
   out_8775703397352493346[3] = -nom_x[3] + true_x[3];
   out_8775703397352493346[4] = -nom_x[4] + true_x[4];
   out_8775703397352493346[5] = -nom_x[5] + true_x[5];
   out_8775703397352493346[6] = -nom_x[6] + true_x[6];
   out_8775703397352493346[7] = -nom_x[7] + true_x[7];
   out_8775703397352493346[8] = -nom_x[8] + true_x[8];
}
void H_mod_fun(double *state, double *out_4824001013826507625) {
   out_4824001013826507625[0] = 1.0;
   out_4824001013826507625[1] = 0.0;
   out_4824001013826507625[2] = 0.0;
   out_4824001013826507625[3] = 0.0;
   out_4824001013826507625[4] = 0.0;
   out_4824001013826507625[5] = 0.0;
   out_4824001013826507625[6] = 0.0;
   out_4824001013826507625[7] = 0.0;
   out_4824001013826507625[8] = 0.0;
   out_4824001013826507625[9] = 0.0;
   out_4824001013826507625[10] = 1.0;
   out_4824001013826507625[11] = 0.0;
   out_4824001013826507625[12] = 0.0;
   out_4824001013826507625[13] = 0.0;
   out_4824001013826507625[14] = 0.0;
   out_4824001013826507625[15] = 0.0;
   out_4824001013826507625[16] = 0.0;
   out_4824001013826507625[17] = 0.0;
   out_4824001013826507625[18] = 0.0;
   out_4824001013826507625[19] = 0.0;
   out_4824001013826507625[20] = 1.0;
   out_4824001013826507625[21] = 0.0;
   out_4824001013826507625[22] = 0.0;
   out_4824001013826507625[23] = 0.0;
   out_4824001013826507625[24] = 0.0;
   out_4824001013826507625[25] = 0.0;
   out_4824001013826507625[26] = 0.0;
   out_4824001013826507625[27] = 0.0;
   out_4824001013826507625[28] = 0.0;
   out_4824001013826507625[29] = 0.0;
   out_4824001013826507625[30] = 1.0;
   out_4824001013826507625[31] = 0.0;
   out_4824001013826507625[32] = 0.0;
   out_4824001013826507625[33] = 0.0;
   out_4824001013826507625[34] = 0.0;
   out_4824001013826507625[35] = 0.0;
   out_4824001013826507625[36] = 0.0;
   out_4824001013826507625[37] = 0.0;
   out_4824001013826507625[38] = 0.0;
   out_4824001013826507625[39] = 0.0;
   out_4824001013826507625[40] = 1.0;
   out_4824001013826507625[41] = 0.0;
   out_4824001013826507625[42] = 0.0;
   out_4824001013826507625[43] = 0.0;
   out_4824001013826507625[44] = 0.0;
   out_4824001013826507625[45] = 0.0;
   out_4824001013826507625[46] = 0.0;
   out_4824001013826507625[47] = 0.0;
   out_4824001013826507625[48] = 0.0;
   out_4824001013826507625[49] = 0.0;
   out_4824001013826507625[50] = 1.0;
   out_4824001013826507625[51] = 0.0;
   out_4824001013826507625[52] = 0.0;
   out_4824001013826507625[53] = 0.0;
   out_4824001013826507625[54] = 0.0;
   out_4824001013826507625[55] = 0.0;
   out_4824001013826507625[56] = 0.0;
   out_4824001013826507625[57] = 0.0;
   out_4824001013826507625[58] = 0.0;
   out_4824001013826507625[59] = 0.0;
   out_4824001013826507625[60] = 1.0;
   out_4824001013826507625[61] = 0.0;
   out_4824001013826507625[62] = 0.0;
   out_4824001013826507625[63] = 0.0;
   out_4824001013826507625[64] = 0.0;
   out_4824001013826507625[65] = 0.0;
   out_4824001013826507625[66] = 0.0;
   out_4824001013826507625[67] = 0.0;
   out_4824001013826507625[68] = 0.0;
   out_4824001013826507625[69] = 0.0;
   out_4824001013826507625[70] = 1.0;
   out_4824001013826507625[71] = 0.0;
   out_4824001013826507625[72] = 0.0;
   out_4824001013826507625[73] = 0.0;
   out_4824001013826507625[74] = 0.0;
   out_4824001013826507625[75] = 0.0;
   out_4824001013826507625[76] = 0.0;
   out_4824001013826507625[77] = 0.0;
   out_4824001013826507625[78] = 0.0;
   out_4824001013826507625[79] = 0.0;
   out_4824001013826507625[80] = 1.0;
}
void f_fun(double *state, double dt, double *out_6057528982108647722) {
   out_6057528982108647722[0] = state[0];
   out_6057528982108647722[1] = state[1];
   out_6057528982108647722[2] = state[2];
   out_6057528982108647722[3] = state[3];
   out_6057528982108647722[4] = state[4];
   out_6057528982108647722[5] = dt*((-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]))*state[6] - 9.8100000000000005*state[8] + stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*state[1]) + (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*state[4])) + state[5];
   out_6057528982108647722[6] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*state[4])) + state[6];
   out_6057528982108647722[7] = state[7];
   out_6057528982108647722[8] = state[8];
}
void F_fun(double *state, double dt, double *out_178208695147004115) {
   out_178208695147004115[0] = 1;
   out_178208695147004115[1] = 0;
   out_178208695147004115[2] = 0;
   out_178208695147004115[3] = 0;
   out_178208695147004115[4] = 0;
   out_178208695147004115[5] = 0;
   out_178208695147004115[6] = 0;
   out_178208695147004115[7] = 0;
   out_178208695147004115[8] = 0;
   out_178208695147004115[9] = 0;
   out_178208695147004115[10] = 1;
   out_178208695147004115[11] = 0;
   out_178208695147004115[12] = 0;
   out_178208695147004115[13] = 0;
   out_178208695147004115[14] = 0;
   out_178208695147004115[15] = 0;
   out_178208695147004115[16] = 0;
   out_178208695147004115[17] = 0;
   out_178208695147004115[18] = 0;
   out_178208695147004115[19] = 0;
   out_178208695147004115[20] = 1;
   out_178208695147004115[21] = 0;
   out_178208695147004115[22] = 0;
   out_178208695147004115[23] = 0;
   out_178208695147004115[24] = 0;
   out_178208695147004115[25] = 0;
   out_178208695147004115[26] = 0;
   out_178208695147004115[27] = 0;
   out_178208695147004115[28] = 0;
   out_178208695147004115[29] = 0;
   out_178208695147004115[30] = 1;
   out_178208695147004115[31] = 0;
   out_178208695147004115[32] = 0;
   out_178208695147004115[33] = 0;
   out_178208695147004115[34] = 0;
   out_178208695147004115[35] = 0;
   out_178208695147004115[36] = 0;
   out_178208695147004115[37] = 0;
   out_178208695147004115[38] = 0;
   out_178208695147004115[39] = 0;
   out_178208695147004115[40] = 1;
   out_178208695147004115[41] = 0;
   out_178208695147004115[42] = 0;
   out_178208695147004115[43] = 0;
   out_178208695147004115[44] = 0;
   out_178208695147004115[45] = dt*(stiffness_front*(-state[2] - state[3] + state[7])/(mass*state[1]) + (-stiffness_front - stiffness_rear)*state[5]/(mass*state[4]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[6]/(mass*state[4]));
   out_178208695147004115[46] = -dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*pow(state[1], 2));
   out_178208695147004115[47] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_178208695147004115[48] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_178208695147004115[49] = dt*((-1 - (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*pow(state[4], 2)))*state[6] - (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*pow(state[4], 2)));
   out_178208695147004115[50] = dt*(-stiffness_front*state[0] - stiffness_rear*state[0])/(mass*state[4]) + 1;
   out_178208695147004115[51] = dt*(-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]));
   out_178208695147004115[52] = dt*stiffness_front*state[0]/(mass*state[1]);
   out_178208695147004115[53] = -9.8100000000000005*dt;
   out_178208695147004115[54] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front - pow(center_to_rear, 2)*stiffness_rear)*state[6]/(rotational_inertia*state[4]));
   out_178208695147004115[55] = -center_to_front*dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*pow(state[1], 2));
   out_178208695147004115[56] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_178208695147004115[57] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_178208695147004115[58] = dt*(-(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*pow(state[4], 2)) - (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*pow(state[4], 2)));
   out_178208695147004115[59] = dt*(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(rotational_inertia*state[4]);
   out_178208695147004115[60] = dt*(-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])/(rotational_inertia*state[4]) + 1;
   out_178208695147004115[61] = center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_178208695147004115[62] = 0;
   out_178208695147004115[63] = 0;
   out_178208695147004115[64] = 0;
   out_178208695147004115[65] = 0;
   out_178208695147004115[66] = 0;
   out_178208695147004115[67] = 0;
   out_178208695147004115[68] = 0;
   out_178208695147004115[69] = 0;
   out_178208695147004115[70] = 1;
   out_178208695147004115[71] = 0;
   out_178208695147004115[72] = 0;
   out_178208695147004115[73] = 0;
   out_178208695147004115[74] = 0;
   out_178208695147004115[75] = 0;
   out_178208695147004115[76] = 0;
   out_178208695147004115[77] = 0;
   out_178208695147004115[78] = 0;
   out_178208695147004115[79] = 0;
   out_178208695147004115[80] = 1;
}
void h_25(double *state, double *unused, double *out_8550702395314279714) {
   out_8550702395314279714[0] = state[6];
}
void H_25(double *state, double *unused, double *out_2187444088811081431) {
   out_2187444088811081431[0] = 0;
   out_2187444088811081431[1] = 0;
   out_2187444088811081431[2] = 0;
   out_2187444088811081431[3] = 0;
   out_2187444088811081431[4] = 0;
   out_2187444088811081431[5] = 0;
   out_2187444088811081431[6] = 1;
   out_2187444088811081431[7] = 0;
   out_2187444088811081431[8] = 0;
}
void h_24(double *state, double *unused, double *out_3063831735768408815) {
   out_3063831735768408815[0] = state[4];
   out_3063831735768408815[1] = state[5];
}
void H_24(double *state, double *unused, double *out_5745369687613404567) {
   out_5745369687613404567[0] = 0;
   out_5745369687613404567[1] = 0;
   out_5745369687613404567[2] = 0;
   out_5745369687613404567[3] = 0;
   out_5745369687613404567[4] = 1;
   out_5745369687613404567[5] = 0;
   out_5745369687613404567[6] = 0;
   out_5745369687613404567[7] = 0;
   out_5745369687613404567[8] = 0;
   out_5745369687613404567[9] = 0;
   out_5745369687613404567[10] = 0;
   out_5745369687613404567[11] = 0;
   out_5745369687613404567[12] = 0;
   out_5745369687613404567[13] = 0;
   out_5745369687613404567[14] = 1;
   out_5745369687613404567[15] = 0;
   out_5745369687613404567[16] = 0;
   out_5745369687613404567[17] = 0;
}
void h_30(double *state, double *unused, double *out_8471988271360455383) {
   out_8471988271360455383[0] = state[4];
}
void H_30(double *state, double *unused, double *out_2340252241316526767) {
   out_2340252241316526767[0] = 0;
   out_2340252241316526767[1] = 0;
   out_2340252241316526767[2] = 0;
   out_2340252241316526767[3] = 0;
   out_2340252241316526767[4] = 1;
   out_2340252241316526767[5] = 0;
   out_2340252241316526767[6] = 0;
   out_2340252241316526767[7] = 0;
   out_2340252241316526767[8] = 0;
}
void h_26(double *state, double *unused, double *out_3470315003699609738) {
   out_3470315003699609738[0] = state[7];
}
void H_26(double *state, double *unused, double *out_1554059230062974793) {
   out_1554059230062974793[0] = 0;
   out_1554059230062974793[1] = 0;
   out_1554059230062974793[2] = 0;
   out_1554059230062974793[3] = 0;
   out_1554059230062974793[4] = 0;
   out_1554059230062974793[5] = 0;
   out_1554059230062974793[6] = 0;
   out_1554059230062974793[7] = 1;
   out_1554059230062974793[8] = 0;
}
void h_27(double *state, double *unused, double *out_4147430428900407732) {
   out_4147430428900407732[0] = state[3];
}
void H_27(double *state, double *unused, double *out_4515015553116951678) {
   out_4515015553116951678[0] = 0;
   out_4515015553116951678[1] = 0;
   out_4515015553116951678[2] = 0;
   out_4515015553116951678[3] = 1;
   out_4515015553116951678[4] = 0;
   out_4515015553116951678[5] = 0;
   out_4515015553116951678[6] = 0;
   out_4515015553116951678[7] = 0;
   out_4515015553116951678[8] = 0;
}
void h_29(double *state, double *unused, double *out_6344999803363309424) {
   out_6344999803363309424[0] = state[1];
}
void H_29(double *state, double *unused, double *out_1830020897002134583) {
   out_1830020897002134583[0] = 0;
   out_1830020897002134583[1] = 1;
   out_1830020897002134583[2] = 0;
   out_1830020897002134583[3] = 0;
   out_1830020897002134583[4] = 0;
   out_1830020897002134583[5] = 0;
   out_1830020897002134583[6] = 0;
   out_1830020897002134583[7] = 0;
   out_1830020897002134583[8] = 0;
}
void h_28(double *state, double *unused, double *out_7296550941016078105) {
   out_7296550941016078105[0] = state[0];
}
void H_28(double *state, double *unused, double *out_133609374563191668) {
   out_133609374563191668[0] = 1;
   out_133609374563191668[1] = 0;
   out_133609374563191668[2] = 0;
   out_133609374563191668[3] = 0;
   out_133609374563191668[4] = 0;
   out_133609374563191668[5] = 0;
   out_133609374563191668[6] = 0;
   out_133609374563191668[7] = 0;
   out_133609374563191668[8] = 0;
}
void h_31(double *state, double *unused, double *out_2132776421574599461) {
   out_2132776421574599461[0] = state[8];
}
void H_31(double *state, double *unused, double *out_2180267332296326269) {
   out_2180267332296326269[0] = 0;
   out_2180267332296326269[1] = 0;
   out_2180267332296326269[2] = 0;
   out_2180267332296326269[3] = 0;
   out_2180267332296326269[4] = 0;
   out_2180267332296326269[5] = 0;
   out_2180267332296326269[6] = 0;
   out_2180267332296326269[7] = 0;
   out_2180267332296326269[8] = 1;
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
void car_err_fun(double *nom_x, double *delta_x, double *out_3392229238668395910) {
  err_fun(nom_x, delta_x, out_3392229238668395910);
}
void car_inv_err_fun(double *nom_x, double *true_x, double *out_8775703397352493346) {
  inv_err_fun(nom_x, true_x, out_8775703397352493346);
}
void car_H_mod_fun(double *state, double *out_4824001013826507625) {
  H_mod_fun(state, out_4824001013826507625);
}
void car_f_fun(double *state, double dt, double *out_6057528982108647722) {
  f_fun(state,  dt, out_6057528982108647722);
}
void car_F_fun(double *state, double dt, double *out_178208695147004115) {
  F_fun(state,  dt, out_178208695147004115);
}
void car_h_25(double *state, double *unused, double *out_8550702395314279714) {
  h_25(state, unused, out_8550702395314279714);
}
void car_H_25(double *state, double *unused, double *out_2187444088811081431) {
  H_25(state, unused, out_2187444088811081431);
}
void car_h_24(double *state, double *unused, double *out_3063831735768408815) {
  h_24(state, unused, out_3063831735768408815);
}
void car_H_24(double *state, double *unused, double *out_5745369687613404567) {
  H_24(state, unused, out_5745369687613404567);
}
void car_h_30(double *state, double *unused, double *out_8471988271360455383) {
  h_30(state, unused, out_8471988271360455383);
}
void car_H_30(double *state, double *unused, double *out_2340252241316526767) {
  H_30(state, unused, out_2340252241316526767);
}
void car_h_26(double *state, double *unused, double *out_3470315003699609738) {
  h_26(state, unused, out_3470315003699609738);
}
void car_H_26(double *state, double *unused, double *out_1554059230062974793) {
  H_26(state, unused, out_1554059230062974793);
}
void car_h_27(double *state, double *unused, double *out_4147430428900407732) {
  h_27(state, unused, out_4147430428900407732);
}
void car_H_27(double *state, double *unused, double *out_4515015553116951678) {
  H_27(state, unused, out_4515015553116951678);
}
void car_h_29(double *state, double *unused, double *out_6344999803363309424) {
  h_29(state, unused, out_6344999803363309424);
}
void car_H_29(double *state, double *unused, double *out_1830020897002134583) {
  H_29(state, unused, out_1830020897002134583);
}
void car_h_28(double *state, double *unused, double *out_7296550941016078105) {
  h_28(state, unused, out_7296550941016078105);
}
void car_H_28(double *state, double *unused, double *out_133609374563191668) {
  H_28(state, unused, out_133609374563191668);
}
void car_h_31(double *state, double *unused, double *out_2132776421574599461) {
  h_31(state, unused, out_2132776421574599461);
}
void car_H_31(double *state, double *unused, double *out_2180267332296326269) {
  H_31(state, unused, out_2180267332296326269);
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
