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
void err_fun(double *nom_x, double *delta_x, double *out_999653426340036453) {
   out_999653426340036453[0] = delta_x[0] + nom_x[0];
   out_999653426340036453[1] = delta_x[1] + nom_x[1];
   out_999653426340036453[2] = delta_x[2] + nom_x[2];
   out_999653426340036453[3] = delta_x[3] + nom_x[3];
   out_999653426340036453[4] = delta_x[4] + nom_x[4];
   out_999653426340036453[5] = delta_x[5] + nom_x[5];
   out_999653426340036453[6] = delta_x[6] + nom_x[6];
   out_999653426340036453[7] = delta_x[7] + nom_x[7];
   out_999653426340036453[8] = delta_x[8] + nom_x[8];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_4610969769563887824) {
   out_4610969769563887824[0] = -nom_x[0] + true_x[0];
   out_4610969769563887824[1] = -nom_x[1] + true_x[1];
   out_4610969769563887824[2] = -nom_x[2] + true_x[2];
   out_4610969769563887824[3] = -nom_x[3] + true_x[3];
   out_4610969769563887824[4] = -nom_x[4] + true_x[4];
   out_4610969769563887824[5] = -nom_x[5] + true_x[5];
   out_4610969769563887824[6] = -nom_x[6] + true_x[6];
   out_4610969769563887824[7] = -nom_x[7] + true_x[7];
   out_4610969769563887824[8] = -nom_x[8] + true_x[8];
}
void H_mod_fun(double *state, double *out_1018390387391438144) {
   out_1018390387391438144[0] = 1.0;
   out_1018390387391438144[1] = 0.0;
   out_1018390387391438144[2] = 0.0;
   out_1018390387391438144[3] = 0.0;
   out_1018390387391438144[4] = 0.0;
   out_1018390387391438144[5] = 0.0;
   out_1018390387391438144[6] = 0.0;
   out_1018390387391438144[7] = 0.0;
   out_1018390387391438144[8] = 0.0;
   out_1018390387391438144[9] = 0.0;
   out_1018390387391438144[10] = 1.0;
   out_1018390387391438144[11] = 0.0;
   out_1018390387391438144[12] = 0.0;
   out_1018390387391438144[13] = 0.0;
   out_1018390387391438144[14] = 0.0;
   out_1018390387391438144[15] = 0.0;
   out_1018390387391438144[16] = 0.0;
   out_1018390387391438144[17] = 0.0;
   out_1018390387391438144[18] = 0.0;
   out_1018390387391438144[19] = 0.0;
   out_1018390387391438144[20] = 1.0;
   out_1018390387391438144[21] = 0.0;
   out_1018390387391438144[22] = 0.0;
   out_1018390387391438144[23] = 0.0;
   out_1018390387391438144[24] = 0.0;
   out_1018390387391438144[25] = 0.0;
   out_1018390387391438144[26] = 0.0;
   out_1018390387391438144[27] = 0.0;
   out_1018390387391438144[28] = 0.0;
   out_1018390387391438144[29] = 0.0;
   out_1018390387391438144[30] = 1.0;
   out_1018390387391438144[31] = 0.0;
   out_1018390387391438144[32] = 0.0;
   out_1018390387391438144[33] = 0.0;
   out_1018390387391438144[34] = 0.0;
   out_1018390387391438144[35] = 0.0;
   out_1018390387391438144[36] = 0.0;
   out_1018390387391438144[37] = 0.0;
   out_1018390387391438144[38] = 0.0;
   out_1018390387391438144[39] = 0.0;
   out_1018390387391438144[40] = 1.0;
   out_1018390387391438144[41] = 0.0;
   out_1018390387391438144[42] = 0.0;
   out_1018390387391438144[43] = 0.0;
   out_1018390387391438144[44] = 0.0;
   out_1018390387391438144[45] = 0.0;
   out_1018390387391438144[46] = 0.0;
   out_1018390387391438144[47] = 0.0;
   out_1018390387391438144[48] = 0.0;
   out_1018390387391438144[49] = 0.0;
   out_1018390387391438144[50] = 1.0;
   out_1018390387391438144[51] = 0.0;
   out_1018390387391438144[52] = 0.0;
   out_1018390387391438144[53] = 0.0;
   out_1018390387391438144[54] = 0.0;
   out_1018390387391438144[55] = 0.0;
   out_1018390387391438144[56] = 0.0;
   out_1018390387391438144[57] = 0.0;
   out_1018390387391438144[58] = 0.0;
   out_1018390387391438144[59] = 0.0;
   out_1018390387391438144[60] = 1.0;
   out_1018390387391438144[61] = 0.0;
   out_1018390387391438144[62] = 0.0;
   out_1018390387391438144[63] = 0.0;
   out_1018390387391438144[64] = 0.0;
   out_1018390387391438144[65] = 0.0;
   out_1018390387391438144[66] = 0.0;
   out_1018390387391438144[67] = 0.0;
   out_1018390387391438144[68] = 0.0;
   out_1018390387391438144[69] = 0.0;
   out_1018390387391438144[70] = 1.0;
   out_1018390387391438144[71] = 0.0;
   out_1018390387391438144[72] = 0.0;
   out_1018390387391438144[73] = 0.0;
   out_1018390387391438144[74] = 0.0;
   out_1018390387391438144[75] = 0.0;
   out_1018390387391438144[76] = 0.0;
   out_1018390387391438144[77] = 0.0;
   out_1018390387391438144[78] = 0.0;
   out_1018390387391438144[79] = 0.0;
   out_1018390387391438144[80] = 1.0;
}
void f_fun(double *state, double dt, double *out_8437871470129484601) {
   out_8437871470129484601[0] = state[0];
   out_8437871470129484601[1] = state[1];
   out_8437871470129484601[2] = state[2];
   out_8437871470129484601[3] = state[3];
   out_8437871470129484601[4] = state[4];
   out_8437871470129484601[5] = dt*((-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]))*state[6] - 9.8100000000000005*state[8] + stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*state[1]) + (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*state[4])) + state[5];
   out_8437871470129484601[6] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*state[4])) + state[6];
   out_8437871470129484601[7] = state[7];
   out_8437871470129484601[8] = state[8];
}
void F_fun(double *state, double dt, double *out_7222922179546997313) {
   out_7222922179546997313[0] = 1;
   out_7222922179546997313[1] = 0;
   out_7222922179546997313[2] = 0;
   out_7222922179546997313[3] = 0;
   out_7222922179546997313[4] = 0;
   out_7222922179546997313[5] = 0;
   out_7222922179546997313[6] = 0;
   out_7222922179546997313[7] = 0;
   out_7222922179546997313[8] = 0;
   out_7222922179546997313[9] = 0;
   out_7222922179546997313[10] = 1;
   out_7222922179546997313[11] = 0;
   out_7222922179546997313[12] = 0;
   out_7222922179546997313[13] = 0;
   out_7222922179546997313[14] = 0;
   out_7222922179546997313[15] = 0;
   out_7222922179546997313[16] = 0;
   out_7222922179546997313[17] = 0;
   out_7222922179546997313[18] = 0;
   out_7222922179546997313[19] = 0;
   out_7222922179546997313[20] = 1;
   out_7222922179546997313[21] = 0;
   out_7222922179546997313[22] = 0;
   out_7222922179546997313[23] = 0;
   out_7222922179546997313[24] = 0;
   out_7222922179546997313[25] = 0;
   out_7222922179546997313[26] = 0;
   out_7222922179546997313[27] = 0;
   out_7222922179546997313[28] = 0;
   out_7222922179546997313[29] = 0;
   out_7222922179546997313[30] = 1;
   out_7222922179546997313[31] = 0;
   out_7222922179546997313[32] = 0;
   out_7222922179546997313[33] = 0;
   out_7222922179546997313[34] = 0;
   out_7222922179546997313[35] = 0;
   out_7222922179546997313[36] = 0;
   out_7222922179546997313[37] = 0;
   out_7222922179546997313[38] = 0;
   out_7222922179546997313[39] = 0;
   out_7222922179546997313[40] = 1;
   out_7222922179546997313[41] = 0;
   out_7222922179546997313[42] = 0;
   out_7222922179546997313[43] = 0;
   out_7222922179546997313[44] = 0;
   out_7222922179546997313[45] = dt*(stiffness_front*(-state[2] - state[3] + state[7])/(mass*state[1]) + (-stiffness_front - stiffness_rear)*state[5]/(mass*state[4]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[6]/(mass*state[4]));
   out_7222922179546997313[46] = -dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*pow(state[1], 2));
   out_7222922179546997313[47] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_7222922179546997313[48] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_7222922179546997313[49] = dt*((-1 - (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*pow(state[4], 2)))*state[6] - (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*pow(state[4], 2)));
   out_7222922179546997313[50] = dt*(-stiffness_front*state[0] - stiffness_rear*state[0])/(mass*state[4]) + 1;
   out_7222922179546997313[51] = dt*(-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]));
   out_7222922179546997313[52] = dt*stiffness_front*state[0]/(mass*state[1]);
   out_7222922179546997313[53] = -9.8100000000000005*dt;
   out_7222922179546997313[54] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front - pow(center_to_rear, 2)*stiffness_rear)*state[6]/(rotational_inertia*state[4]));
   out_7222922179546997313[55] = -center_to_front*dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*pow(state[1], 2));
   out_7222922179546997313[56] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_7222922179546997313[57] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_7222922179546997313[58] = dt*(-(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*pow(state[4], 2)) - (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*pow(state[4], 2)));
   out_7222922179546997313[59] = dt*(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(rotational_inertia*state[4]);
   out_7222922179546997313[60] = dt*(-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])/(rotational_inertia*state[4]) + 1;
   out_7222922179546997313[61] = center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_7222922179546997313[62] = 0;
   out_7222922179546997313[63] = 0;
   out_7222922179546997313[64] = 0;
   out_7222922179546997313[65] = 0;
   out_7222922179546997313[66] = 0;
   out_7222922179546997313[67] = 0;
   out_7222922179546997313[68] = 0;
   out_7222922179546997313[69] = 0;
   out_7222922179546997313[70] = 1;
   out_7222922179546997313[71] = 0;
   out_7222922179546997313[72] = 0;
   out_7222922179546997313[73] = 0;
   out_7222922179546997313[74] = 0;
   out_7222922179546997313[75] = 0;
   out_7222922179546997313[76] = 0;
   out_7222922179546997313[77] = 0;
   out_7222922179546997313[78] = 0;
   out_7222922179546997313[79] = 0;
   out_7222922179546997313[80] = 1;
}
void h_25(double *state, double *unused, double *out_5845994727189855200) {
   out_5845994727189855200[0] = state[6];
}
void H_25(double *state, double *unused, double *out_1618166537623988050) {
   out_1618166537623988050[0] = 0;
   out_1618166537623988050[1] = 0;
   out_1618166537623988050[2] = 0;
   out_1618166537623988050[3] = 0;
   out_1618166537623988050[4] = 0;
   out_1618166537623988050[5] = 0;
   out_1618166537623988050[6] = 1;
   out_1618166537623988050[7] = 0;
   out_1618166537623988050[8] = 0;
}
void h_24(double *state, double *unused, double *out_2733857150290724482) {
   out_2733857150290724482[0] = state[4];
   out_2733857150290724482[1] = state[5];
}
void H_24(double *state, double *unused, double *out_3790816136629487616) {
   out_3790816136629487616[0] = 0;
   out_3790816136629487616[1] = 0;
   out_3790816136629487616[2] = 0;
   out_3790816136629487616[3] = 0;
   out_3790816136629487616[4] = 1;
   out_3790816136629487616[5] = 0;
   out_3790816136629487616[6] = 0;
   out_3790816136629487616[7] = 0;
   out_3790816136629487616[8] = 0;
   out_3790816136629487616[9] = 0;
   out_3790816136629487616[10] = 0;
   out_3790816136629487616[11] = 0;
   out_3790816136629487616[12] = 0;
   out_3790816136629487616[13] = 0;
   out_3790816136629487616[14] = 1;
   out_3790816136629487616[15] = 0;
   out_3790816136629487616[16] = 0;
   out_3790816136629487616[17] = 0;
}
void h_30(double *state, double *unused, double *out_2208545198935832850) {
   out_2208545198935832850[0] = state[4];
}
void H_30(double *state, double *unused, double *out_6145862867751596248) {
   out_6145862867751596248[0] = 0;
   out_6145862867751596248[1] = 0;
   out_6145862867751596248[2] = 0;
   out_6145862867751596248[3] = 0;
   out_6145862867751596248[4] = 1;
   out_6145862867751596248[5] = 0;
   out_6145862867751596248[6] = 0;
   out_6145862867751596248[7] = 0;
   out_6145862867751596248[8] = 0;
}
void h_26(double *state, double *unused, double *out_604006904334020533) {
   out_604006904334020533[0] = state[7];
}
void H_26(double *state, double *unused, double *out_5359669856498044274) {
   out_5359669856498044274[0] = 0;
   out_5359669856498044274[1] = 0;
   out_5359669856498044274[2] = 0;
   out_5359669856498044274[3] = 0;
   out_5359669856498044274[4] = 0;
   out_5359669856498044274[5] = 0;
   out_5359669856498044274[6] = 0;
   out_5359669856498044274[7] = 1;
   out_5359669856498044274[8] = 0;
}
void h_27(double *state, double *unused, double *out_844321459529009555) {
   out_844321459529009555[0] = state[3];
}
void H_27(double *state, double *unused, double *out_8320626179552021159) {
   out_8320626179552021159[0] = 0;
   out_8320626179552021159[1] = 0;
   out_8320626179552021159[2] = 0;
   out_8320626179552021159[3] = 1;
   out_8320626179552021159[4] = 0;
   out_8320626179552021159[5] = 0;
   out_8320626179552021159[6] = 0;
   out_8320626179552021159[7] = 0;
   out_8320626179552021159[8] = 0;
}
void h_29(double *state, double *unused, double *out_2793128068725012795) {
   out_2793128068725012795[0] = state[1];
}
void H_29(double *state, double *unused, double *out_5635631523437204064) {
   out_5635631523437204064[0] = 0;
   out_5635631523437204064[1] = 1;
   out_5635631523437204064[2] = 0;
   out_5635631523437204064[3] = 0;
   out_5635631523437204064[4] = 0;
   out_5635631523437204064[5] = 0;
   out_5635631523437204064[6] = 0;
   out_5635631523437204064[7] = 0;
   out_5635631523437204064[8] = 0;
}
void h_28(double *state, double *unused, double *out_4484828697352558873) {
   out_4484828697352558873[0] = state[0];
}
void H_28(double *state, double *unused, double *out_3672001251871877813) {
   out_3672001251871877813[0] = 1;
   out_3672001251871877813[1] = 0;
   out_3672001251871877813[2] = 0;
   out_3672001251871877813[3] = 0;
   out_3672001251871877813[4] = 0;
   out_3672001251871877813[5] = 0;
   out_3672001251871877813[6] = 0;
   out_3672001251871877813[7] = 0;
   out_3672001251871877813[8] = 0;
}
void h_31(double *state, double *unused, double *out_2046944767694668159) {
   out_2046944767694668159[0] = state[8];
}
void H_31(double *state, double *unused, double *out_5985877958731395750) {
   out_5985877958731395750[0] = 0;
   out_5985877958731395750[1] = 0;
   out_5985877958731395750[2] = 0;
   out_5985877958731395750[3] = 0;
   out_5985877958731395750[4] = 0;
   out_5985877958731395750[5] = 0;
   out_5985877958731395750[6] = 0;
   out_5985877958731395750[7] = 0;
   out_5985877958731395750[8] = 1;
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
void car_err_fun(double *nom_x, double *delta_x, double *out_999653426340036453) {
  err_fun(nom_x, delta_x, out_999653426340036453);
}
void car_inv_err_fun(double *nom_x, double *true_x, double *out_4610969769563887824) {
  inv_err_fun(nom_x, true_x, out_4610969769563887824);
}
void car_H_mod_fun(double *state, double *out_1018390387391438144) {
  H_mod_fun(state, out_1018390387391438144);
}
void car_f_fun(double *state, double dt, double *out_8437871470129484601) {
  f_fun(state,  dt, out_8437871470129484601);
}
void car_F_fun(double *state, double dt, double *out_7222922179546997313) {
  F_fun(state,  dt, out_7222922179546997313);
}
void car_h_25(double *state, double *unused, double *out_5845994727189855200) {
  h_25(state, unused, out_5845994727189855200);
}
void car_H_25(double *state, double *unused, double *out_1618166537623988050) {
  H_25(state, unused, out_1618166537623988050);
}
void car_h_24(double *state, double *unused, double *out_2733857150290724482) {
  h_24(state, unused, out_2733857150290724482);
}
void car_H_24(double *state, double *unused, double *out_3790816136629487616) {
  H_24(state, unused, out_3790816136629487616);
}
void car_h_30(double *state, double *unused, double *out_2208545198935832850) {
  h_30(state, unused, out_2208545198935832850);
}
void car_H_30(double *state, double *unused, double *out_6145862867751596248) {
  H_30(state, unused, out_6145862867751596248);
}
void car_h_26(double *state, double *unused, double *out_604006904334020533) {
  h_26(state, unused, out_604006904334020533);
}
void car_H_26(double *state, double *unused, double *out_5359669856498044274) {
  H_26(state, unused, out_5359669856498044274);
}
void car_h_27(double *state, double *unused, double *out_844321459529009555) {
  h_27(state, unused, out_844321459529009555);
}
void car_H_27(double *state, double *unused, double *out_8320626179552021159) {
  H_27(state, unused, out_8320626179552021159);
}
void car_h_29(double *state, double *unused, double *out_2793128068725012795) {
  h_29(state, unused, out_2793128068725012795);
}
void car_H_29(double *state, double *unused, double *out_5635631523437204064) {
  H_29(state, unused, out_5635631523437204064);
}
void car_h_28(double *state, double *unused, double *out_4484828697352558873) {
  h_28(state, unused, out_4484828697352558873);
}
void car_H_28(double *state, double *unused, double *out_3672001251871877813) {
  H_28(state, unused, out_3672001251871877813);
}
void car_h_31(double *state, double *unused, double *out_2046944767694668159) {
  h_31(state, unused, out_2046944767694668159);
}
void car_H_31(double *state, double *unused, double *out_5985877958731395750) {
  H_31(state, unused, out_5985877958731395750);
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
