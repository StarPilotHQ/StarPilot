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
void err_fun(double *nom_x, double *delta_x, double *out_6797865572154616298) {
   out_6797865572154616298[0] = delta_x[0] + nom_x[0];
   out_6797865572154616298[1] = delta_x[1] + nom_x[1];
   out_6797865572154616298[2] = delta_x[2] + nom_x[2];
   out_6797865572154616298[3] = delta_x[3] + nom_x[3];
   out_6797865572154616298[4] = delta_x[4] + nom_x[4];
   out_6797865572154616298[5] = delta_x[5] + nom_x[5];
   out_6797865572154616298[6] = delta_x[6] + nom_x[6];
   out_6797865572154616298[7] = delta_x[7] + nom_x[7];
   out_6797865572154616298[8] = delta_x[8] + nom_x[8];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_5593486999645366592) {
   out_5593486999645366592[0] = -nom_x[0] + true_x[0];
   out_5593486999645366592[1] = -nom_x[1] + true_x[1];
   out_5593486999645366592[2] = -nom_x[2] + true_x[2];
   out_5593486999645366592[3] = -nom_x[3] + true_x[3];
   out_5593486999645366592[4] = -nom_x[4] + true_x[4];
   out_5593486999645366592[5] = -nom_x[5] + true_x[5];
   out_5593486999645366592[6] = -nom_x[6] + true_x[6];
   out_5593486999645366592[7] = -nom_x[7] + true_x[7];
   out_5593486999645366592[8] = -nom_x[8] + true_x[8];
}
void H_mod_fun(double *state, double *out_1621514070430669622) {
   out_1621514070430669622[0] = 1.0;
   out_1621514070430669622[1] = 0.0;
   out_1621514070430669622[2] = 0.0;
   out_1621514070430669622[3] = 0.0;
   out_1621514070430669622[4] = 0.0;
   out_1621514070430669622[5] = 0.0;
   out_1621514070430669622[6] = 0.0;
   out_1621514070430669622[7] = 0.0;
   out_1621514070430669622[8] = 0.0;
   out_1621514070430669622[9] = 0.0;
   out_1621514070430669622[10] = 1.0;
   out_1621514070430669622[11] = 0.0;
   out_1621514070430669622[12] = 0.0;
   out_1621514070430669622[13] = 0.0;
   out_1621514070430669622[14] = 0.0;
   out_1621514070430669622[15] = 0.0;
   out_1621514070430669622[16] = 0.0;
   out_1621514070430669622[17] = 0.0;
   out_1621514070430669622[18] = 0.0;
   out_1621514070430669622[19] = 0.0;
   out_1621514070430669622[20] = 1.0;
   out_1621514070430669622[21] = 0.0;
   out_1621514070430669622[22] = 0.0;
   out_1621514070430669622[23] = 0.0;
   out_1621514070430669622[24] = 0.0;
   out_1621514070430669622[25] = 0.0;
   out_1621514070430669622[26] = 0.0;
   out_1621514070430669622[27] = 0.0;
   out_1621514070430669622[28] = 0.0;
   out_1621514070430669622[29] = 0.0;
   out_1621514070430669622[30] = 1.0;
   out_1621514070430669622[31] = 0.0;
   out_1621514070430669622[32] = 0.0;
   out_1621514070430669622[33] = 0.0;
   out_1621514070430669622[34] = 0.0;
   out_1621514070430669622[35] = 0.0;
   out_1621514070430669622[36] = 0.0;
   out_1621514070430669622[37] = 0.0;
   out_1621514070430669622[38] = 0.0;
   out_1621514070430669622[39] = 0.0;
   out_1621514070430669622[40] = 1.0;
   out_1621514070430669622[41] = 0.0;
   out_1621514070430669622[42] = 0.0;
   out_1621514070430669622[43] = 0.0;
   out_1621514070430669622[44] = 0.0;
   out_1621514070430669622[45] = 0.0;
   out_1621514070430669622[46] = 0.0;
   out_1621514070430669622[47] = 0.0;
   out_1621514070430669622[48] = 0.0;
   out_1621514070430669622[49] = 0.0;
   out_1621514070430669622[50] = 1.0;
   out_1621514070430669622[51] = 0.0;
   out_1621514070430669622[52] = 0.0;
   out_1621514070430669622[53] = 0.0;
   out_1621514070430669622[54] = 0.0;
   out_1621514070430669622[55] = 0.0;
   out_1621514070430669622[56] = 0.0;
   out_1621514070430669622[57] = 0.0;
   out_1621514070430669622[58] = 0.0;
   out_1621514070430669622[59] = 0.0;
   out_1621514070430669622[60] = 1.0;
   out_1621514070430669622[61] = 0.0;
   out_1621514070430669622[62] = 0.0;
   out_1621514070430669622[63] = 0.0;
   out_1621514070430669622[64] = 0.0;
   out_1621514070430669622[65] = 0.0;
   out_1621514070430669622[66] = 0.0;
   out_1621514070430669622[67] = 0.0;
   out_1621514070430669622[68] = 0.0;
   out_1621514070430669622[69] = 0.0;
   out_1621514070430669622[70] = 1.0;
   out_1621514070430669622[71] = 0.0;
   out_1621514070430669622[72] = 0.0;
   out_1621514070430669622[73] = 0.0;
   out_1621514070430669622[74] = 0.0;
   out_1621514070430669622[75] = 0.0;
   out_1621514070430669622[76] = 0.0;
   out_1621514070430669622[77] = 0.0;
   out_1621514070430669622[78] = 0.0;
   out_1621514070430669622[79] = 0.0;
   out_1621514070430669622[80] = 1.0;
}
void f_fun(double *state, double dt, double *out_8357351710386191496) {
   out_8357351710386191496[0] = state[0];
   out_8357351710386191496[1] = state[1];
   out_8357351710386191496[2] = state[2];
   out_8357351710386191496[3] = state[3];
   out_8357351710386191496[4] = state[4];
   out_8357351710386191496[5] = dt*((-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]))*state[6] - 9.8100000000000005*state[8] + stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*state[1]) + (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*state[4])) + state[5];
   out_8357351710386191496[6] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*state[4])) + state[6];
   out_8357351710386191496[7] = state[7];
   out_8357351710386191496[8] = state[8];
}
void F_fun(double *state, double dt, double *out_2310875689721956683) {
   out_2310875689721956683[0] = 1;
   out_2310875689721956683[1] = 0;
   out_2310875689721956683[2] = 0;
   out_2310875689721956683[3] = 0;
   out_2310875689721956683[4] = 0;
   out_2310875689721956683[5] = 0;
   out_2310875689721956683[6] = 0;
   out_2310875689721956683[7] = 0;
   out_2310875689721956683[8] = 0;
   out_2310875689721956683[9] = 0;
   out_2310875689721956683[10] = 1;
   out_2310875689721956683[11] = 0;
   out_2310875689721956683[12] = 0;
   out_2310875689721956683[13] = 0;
   out_2310875689721956683[14] = 0;
   out_2310875689721956683[15] = 0;
   out_2310875689721956683[16] = 0;
   out_2310875689721956683[17] = 0;
   out_2310875689721956683[18] = 0;
   out_2310875689721956683[19] = 0;
   out_2310875689721956683[20] = 1;
   out_2310875689721956683[21] = 0;
   out_2310875689721956683[22] = 0;
   out_2310875689721956683[23] = 0;
   out_2310875689721956683[24] = 0;
   out_2310875689721956683[25] = 0;
   out_2310875689721956683[26] = 0;
   out_2310875689721956683[27] = 0;
   out_2310875689721956683[28] = 0;
   out_2310875689721956683[29] = 0;
   out_2310875689721956683[30] = 1;
   out_2310875689721956683[31] = 0;
   out_2310875689721956683[32] = 0;
   out_2310875689721956683[33] = 0;
   out_2310875689721956683[34] = 0;
   out_2310875689721956683[35] = 0;
   out_2310875689721956683[36] = 0;
   out_2310875689721956683[37] = 0;
   out_2310875689721956683[38] = 0;
   out_2310875689721956683[39] = 0;
   out_2310875689721956683[40] = 1;
   out_2310875689721956683[41] = 0;
   out_2310875689721956683[42] = 0;
   out_2310875689721956683[43] = 0;
   out_2310875689721956683[44] = 0;
   out_2310875689721956683[45] = dt*(stiffness_front*(-state[2] - state[3] + state[7])/(mass*state[1]) + (-stiffness_front - stiffness_rear)*state[5]/(mass*state[4]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[6]/(mass*state[4]));
   out_2310875689721956683[46] = -dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*pow(state[1], 2));
   out_2310875689721956683[47] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_2310875689721956683[48] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_2310875689721956683[49] = dt*((-1 - (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*pow(state[4], 2)))*state[6] - (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*pow(state[4], 2)));
   out_2310875689721956683[50] = dt*(-stiffness_front*state[0] - stiffness_rear*state[0])/(mass*state[4]) + 1;
   out_2310875689721956683[51] = dt*(-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]));
   out_2310875689721956683[52] = dt*stiffness_front*state[0]/(mass*state[1]);
   out_2310875689721956683[53] = -9.8100000000000005*dt;
   out_2310875689721956683[54] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front - pow(center_to_rear, 2)*stiffness_rear)*state[6]/(rotational_inertia*state[4]));
   out_2310875689721956683[55] = -center_to_front*dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*pow(state[1], 2));
   out_2310875689721956683[56] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_2310875689721956683[57] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_2310875689721956683[58] = dt*(-(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*pow(state[4], 2)) - (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*pow(state[4], 2)));
   out_2310875689721956683[59] = dt*(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(rotational_inertia*state[4]);
   out_2310875689721956683[60] = dt*(-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])/(rotational_inertia*state[4]) + 1;
   out_2310875689721956683[61] = center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_2310875689721956683[62] = 0;
   out_2310875689721956683[63] = 0;
   out_2310875689721956683[64] = 0;
   out_2310875689721956683[65] = 0;
   out_2310875689721956683[66] = 0;
   out_2310875689721956683[67] = 0;
   out_2310875689721956683[68] = 0;
   out_2310875689721956683[69] = 0;
   out_2310875689721956683[70] = 1;
   out_2310875689721956683[71] = 0;
   out_2310875689721956683[72] = 0;
   out_2310875689721956683[73] = 0;
   out_2310875689721956683[74] = 0;
   out_2310875689721956683[75] = 0;
   out_2310875689721956683[76] = 0;
   out_2310875689721956683[77] = 0;
   out_2310875689721956683[78] = 0;
   out_2310875689721956683[79] = 0;
   out_2310875689721956683[80] = 1;
}
void h_25(double *state, double *unused, double *out_3740638677979033229) {
   out_3740638677979033229[0] = state[6];
}
void H_25(double *state, double *unused, double *out_317174960238698002) {
   out_317174960238698002[0] = 0;
   out_317174960238698002[1] = 0;
   out_317174960238698002[2] = 0;
   out_317174960238698002[3] = 0;
   out_317174960238698002[4] = 0;
   out_317174960238698002[5] = 0;
   out_317174960238698002[6] = 1;
   out_317174960238698002[7] = 0;
   out_317174960238698002[8] = 0;
}
void h_24(double *state, double *unused, double *out_7552560004953043119) {
   out_7552560004953043119[0] = state[4];
   out_7552560004953043119[1] = state[5];
}
void H_24(double *state, double *unused, double *out_4503146544417290261) {
   out_4503146544417290261[0] = 0;
   out_4503146544417290261[1] = 0;
   out_4503146544417290261[2] = 0;
   out_4503146544417290261[3] = 0;
   out_4503146544417290261[4] = 1;
   out_4503146544417290261[5] = 0;
   out_4503146544417290261[6] = 0;
   out_4503146544417290261[7] = 0;
   out_4503146544417290261[8] = 0;
   out_4503146544417290261[9] = 0;
   out_4503146544417290261[10] = 0;
   out_4503146544417290261[11] = 0;
   out_4503146544417290261[12] = 0;
   out_4503146544417290261[13] = 0;
   out_4503146544417290261[14] = 1;
   out_4503146544417290261[15] = 0;
   out_4503146544417290261[16] = 0;
   out_4503146544417290261[17] = 0;
}
void h_30(double *state, double *unused, double *out_5502959985118725288) {
   out_5502959985118725288[0] = state[4];
}
void H_30(double *state, double *unused, double *out_187836013095457932) {
   out_187836013095457932[0] = 0;
   out_187836013095457932[1] = 0;
   out_187836013095457932[2] = 0;
   out_187836013095457932[3] = 0;
   out_187836013095457932[4] = 1;
   out_187836013095457932[5] = 0;
   out_187836013095457932[6] = 0;
   out_187836013095457932[7] = 0;
   out_187836013095457932[8] = 0;
}
void h_26(double *state, double *unused, double *out_8545832007309197316) {
   out_8545832007309197316[0] = state[7];
}
void H_26(double *state, double *unused, double *out_974029024349009906) {
   out_974029024349009906[0] = 0;
   out_974029024349009906[1] = 0;
   out_974029024349009906[2] = 0;
   out_974029024349009906[3] = 0;
   out_974029024349009906[4] = 0;
   out_974029024349009906[5] = 0;
   out_974029024349009906[6] = 0;
   out_974029024349009906[7] = 1;
   out_974029024349009906[8] = 0;
}
void h_27(double *state, double *unused, double *out_5988268475861826061) {
   out_5988268475861826061[0] = state[3];
}
void H_27(double *state, double *unused, double *out_1986927298704966979) {
   out_1986927298704966979[0] = 0;
   out_1986927298704966979[1] = 0;
   out_1986927298704966979[2] = 0;
   out_1986927298704966979[3] = 1;
   out_1986927298704966979[4] = 0;
   out_1986927298704966979[5] = 0;
   out_1986927298704966979[6] = 0;
   out_1986927298704966979[7] = 0;
   out_1986927298704966979[8] = 0;
}
void h_29(double *state, double *unused, double *out_6446469838020387723) {
   out_6446469838020387723[0] = state[1];
}
void H_29(double *state, double *unused, double *out_698067357409850116) {
   out_698067357409850116[0] = 0;
   out_698067357409850116[1] = 1;
   out_698067357409850116[2] = 0;
   out_698067357409850116[3] = 0;
   out_698067357409850116[4] = 0;
   out_698067357409850116[5] = 0;
   out_698067357409850116[6] = 0;
   out_698067357409850116[7] = 0;
   out_698067357409850116[8] = 0;
}
void h_28(double *state, double *unused, double *out_5384952591185348544) {
   out_5384952591185348544[0] = state[0];
}
void H_28(double *state, double *unused, double *out_2661697628975176367) {
   out_2661697628975176367[0] = 1;
   out_2661697628975176367[1] = 0;
   out_2661697628975176367[2] = 0;
   out_2661697628975176367[3] = 0;
   out_2661697628975176367[4] = 0;
   out_2661697628975176367[5] = 0;
   out_2661697628975176367[6] = 0;
   out_2661697628975176367[7] = 0;
   out_2661697628975176367[8] = 0;
}
void h_31(double *state, double *unused, double *out_3226676486701999265) {
   out_3226676486701999265[0] = state[8];
}
void H_31(double *state, double *unused, double *out_347820922115658430) {
   out_347820922115658430[0] = 0;
   out_347820922115658430[1] = 0;
   out_347820922115658430[2] = 0;
   out_347820922115658430[3] = 0;
   out_347820922115658430[4] = 0;
   out_347820922115658430[5] = 0;
   out_347820922115658430[6] = 0;
   out_347820922115658430[7] = 0;
   out_347820922115658430[8] = 1;
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
void car_err_fun(double *nom_x, double *delta_x, double *out_6797865572154616298) {
  err_fun(nom_x, delta_x, out_6797865572154616298);
}
void car_inv_err_fun(double *nom_x, double *true_x, double *out_5593486999645366592) {
  inv_err_fun(nom_x, true_x, out_5593486999645366592);
}
void car_H_mod_fun(double *state, double *out_1621514070430669622) {
  H_mod_fun(state, out_1621514070430669622);
}
void car_f_fun(double *state, double dt, double *out_8357351710386191496) {
  f_fun(state,  dt, out_8357351710386191496);
}
void car_F_fun(double *state, double dt, double *out_2310875689721956683) {
  F_fun(state,  dt, out_2310875689721956683);
}
void car_h_25(double *state, double *unused, double *out_3740638677979033229) {
  h_25(state, unused, out_3740638677979033229);
}
void car_H_25(double *state, double *unused, double *out_317174960238698002) {
  H_25(state, unused, out_317174960238698002);
}
void car_h_24(double *state, double *unused, double *out_7552560004953043119) {
  h_24(state, unused, out_7552560004953043119);
}
void car_H_24(double *state, double *unused, double *out_4503146544417290261) {
  H_24(state, unused, out_4503146544417290261);
}
void car_h_30(double *state, double *unused, double *out_5502959985118725288) {
  h_30(state, unused, out_5502959985118725288);
}
void car_H_30(double *state, double *unused, double *out_187836013095457932) {
  H_30(state, unused, out_187836013095457932);
}
void car_h_26(double *state, double *unused, double *out_8545832007309197316) {
  h_26(state, unused, out_8545832007309197316);
}
void car_H_26(double *state, double *unused, double *out_974029024349009906) {
  H_26(state, unused, out_974029024349009906);
}
void car_h_27(double *state, double *unused, double *out_5988268475861826061) {
  h_27(state, unused, out_5988268475861826061);
}
void car_H_27(double *state, double *unused, double *out_1986927298704966979) {
  H_27(state, unused, out_1986927298704966979);
}
void car_h_29(double *state, double *unused, double *out_6446469838020387723) {
  h_29(state, unused, out_6446469838020387723);
}
void car_H_29(double *state, double *unused, double *out_698067357409850116) {
  H_29(state, unused, out_698067357409850116);
}
void car_h_28(double *state, double *unused, double *out_5384952591185348544) {
  h_28(state, unused, out_5384952591185348544);
}
void car_H_28(double *state, double *unused, double *out_2661697628975176367) {
  H_28(state, unused, out_2661697628975176367);
}
void car_h_31(double *state, double *unused, double *out_3226676486701999265) {
  h_31(state, unused, out_3226676486701999265);
}
void car_H_31(double *state, double *unused, double *out_347820922115658430) {
  H_31(state, unused, out_347820922115658430);
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
