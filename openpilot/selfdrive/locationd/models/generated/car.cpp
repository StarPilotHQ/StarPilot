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
void err_fun(double *nom_x, double *delta_x, double *out_7353202191454035649) {
   out_7353202191454035649[0] = delta_x[0] + nom_x[0];
   out_7353202191454035649[1] = delta_x[1] + nom_x[1];
   out_7353202191454035649[2] = delta_x[2] + nom_x[2];
   out_7353202191454035649[3] = delta_x[3] + nom_x[3];
   out_7353202191454035649[4] = delta_x[4] + nom_x[4];
   out_7353202191454035649[5] = delta_x[5] + nom_x[5];
   out_7353202191454035649[6] = delta_x[6] + nom_x[6];
   out_7353202191454035649[7] = delta_x[7] + nom_x[7];
   out_7353202191454035649[8] = delta_x[8] + nom_x[8];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_5273149315250963980) {
   out_5273149315250963980[0] = -nom_x[0] + true_x[0];
   out_5273149315250963980[1] = -nom_x[1] + true_x[1];
   out_5273149315250963980[2] = -nom_x[2] + true_x[2];
   out_5273149315250963980[3] = -nom_x[3] + true_x[3];
   out_5273149315250963980[4] = -nom_x[4] + true_x[4];
   out_5273149315250963980[5] = -nom_x[5] + true_x[5];
   out_5273149315250963980[6] = -nom_x[6] + true_x[6];
   out_5273149315250963980[7] = -nom_x[7] + true_x[7];
   out_5273149315250963980[8] = -nom_x[8] + true_x[8];
}
void H_mod_fun(double *state, double *out_5463203357507594110) {
   out_5463203357507594110[0] = 1.0;
   out_5463203357507594110[1] = 0.0;
   out_5463203357507594110[2] = 0.0;
   out_5463203357507594110[3] = 0.0;
   out_5463203357507594110[4] = 0.0;
   out_5463203357507594110[5] = 0.0;
   out_5463203357507594110[6] = 0.0;
   out_5463203357507594110[7] = 0.0;
   out_5463203357507594110[8] = 0.0;
   out_5463203357507594110[9] = 0.0;
   out_5463203357507594110[10] = 1.0;
   out_5463203357507594110[11] = 0.0;
   out_5463203357507594110[12] = 0.0;
   out_5463203357507594110[13] = 0.0;
   out_5463203357507594110[14] = 0.0;
   out_5463203357507594110[15] = 0.0;
   out_5463203357507594110[16] = 0.0;
   out_5463203357507594110[17] = 0.0;
   out_5463203357507594110[18] = 0.0;
   out_5463203357507594110[19] = 0.0;
   out_5463203357507594110[20] = 1.0;
   out_5463203357507594110[21] = 0.0;
   out_5463203357507594110[22] = 0.0;
   out_5463203357507594110[23] = 0.0;
   out_5463203357507594110[24] = 0.0;
   out_5463203357507594110[25] = 0.0;
   out_5463203357507594110[26] = 0.0;
   out_5463203357507594110[27] = 0.0;
   out_5463203357507594110[28] = 0.0;
   out_5463203357507594110[29] = 0.0;
   out_5463203357507594110[30] = 1.0;
   out_5463203357507594110[31] = 0.0;
   out_5463203357507594110[32] = 0.0;
   out_5463203357507594110[33] = 0.0;
   out_5463203357507594110[34] = 0.0;
   out_5463203357507594110[35] = 0.0;
   out_5463203357507594110[36] = 0.0;
   out_5463203357507594110[37] = 0.0;
   out_5463203357507594110[38] = 0.0;
   out_5463203357507594110[39] = 0.0;
   out_5463203357507594110[40] = 1.0;
   out_5463203357507594110[41] = 0.0;
   out_5463203357507594110[42] = 0.0;
   out_5463203357507594110[43] = 0.0;
   out_5463203357507594110[44] = 0.0;
   out_5463203357507594110[45] = 0.0;
   out_5463203357507594110[46] = 0.0;
   out_5463203357507594110[47] = 0.0;
   out_5463203357507594110[48] = 0.0;
   out_5463203357507594110[49] = 0.0;
   out_5463203357507594110[50] = 1.0;
   out_5463203357507594110[51] = 0.0;
   out_5463203357507594110[52] = 0.0;
   out_5463203357507594110[53] = 0.0;
   out_5463203357507594110[54] = 0.0;
   out_5463203357507594110[55] = 0.0;
   out_5463203357507594110[56] = 0.0;
   out_5463203357507594110[57] = 0.0;
   out_5463203357507594110[58] = 0.0;
   out_5463203357507594110[59] = 0.0;
   out_5463203357507594110[60] = 1.0;
   out_5463203357507594110[61] = 0.0;
   out_5463203357507594110[62] = 0.0;
   out_5463203357507594110[63] = 0.0;
   out_5463203357507594110[64] = 0.0;
   out_5463203357507594110[65] = 0.0;
   out_5463203357507594110[66] = 0.0;
   out_5463203357507594110[67] = 0.0;
   out_5463203357507594110[68] = 0.0;
   out_5463203357507594110[69] = 0.0;
   out_5463203357507594110[70] = 1.0;
   out_5463203357507594110[71] = 0.0;
   out_5463203357507594110[72] = 0.0;
   out_5463203357507594110[73] = 0.0;
   out_5463203357507594110[74] = 0.0;
   out_5463203357507594110[75] = 0.0;
   out_5463203357507594110[76] = 0.0;
   out_5463203357507594110[77] = 0.0;
   out_5463203357507594110[78] = 0.0;
   out_5463203357507594110[79] = 0.0;
   out_5463203357507594110[80] = 1.0;
}
void f_fun(double *state, double dt, double *out_6125045598488451197) {
   out_6125045598488451197[0] = state[0];
   out_6125045598488451197[1] = state[1];
   out_6125045598488451197[2] = state[2];
   out_6125045598488451197[3] = state[3];
   out_6125045598488451197[4] = state[4];
   out_6125045598488451197[5] = dt*((-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]))*state[6] - 9.8100000000000005*state[8] + stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*state[1]) + (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*state[4])) + state[5];
   out_6125045598488451197[6] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*state[4])) + state[6];
   out_6125045598488451197[7] = state[7];
   out_6125045598488451197[8] = state[8];
}
void F_fun(double *state, double dt, double *out_548031277288491442) {
   out_548031277288491442[0] = 1;
   out_548031277288491442[1] = 0;
   out_548031277288491442[2] = 0;
   out_548031277288491442[3] = 0;
   out_548031277288491442[4] = 0;
   out_548031277288491442[5] = 0;
   out_548031277288491442[6] = 0;
   out_548031277288491442[7] = 0;
   out_548031277288491442[8] = 0;
   out_548031277288491442[9] = 0;
   out_548031277288491442[10] = 1;
   out_548031277288491442[11] = 0;
   out_548031277288491442[12] = 0;
   out_548031277288491442[13] = 0;
   out_548031277288491442[14] = 0;
   out_548031277288491442[15] = 0;
   out_548031277288491442[16] = 0;
   out_548031277288491442[17] = 0;
   out_548031277288491442[18] = 0;
   out_548031277288491442[19] = 0;
   out_548031277288491442[20] = 1;
   out_548031277288491442[21] = 0;
   out_548031277288491442[22] = 0;
   out_548031277288491442[23] = 0;
   out_548031277288491442[24] = 0;
   out_548031277288491442[25] = 0;
   out_548031277288491442[26] = 0;
   out_548031277288491442[27] = 0;
   out_548031277288491442[28] = 0;
   out_548031277288491442[29] = 0;
   out_548031277288491442[30] = 1;
   out_548031277288491442[31] = 0;
   out_548031277288491442[32] = 0;
   out_548031277288491442[33] = 0;
   out_548031277288491442[34] = 0;
   out_548031277288491442[35] = 0;
   out_548031277288491442[36] = 0;
   out_548031277288491442[37] = 0;
   out_548031277288491442[38] = 0;
   out_548031277288491442[39] = 0;
   out_548031277288491442[40] = 1;
   out_548031277288491442[41] = 0;
   out_548031277288491442[42] = 0;
   out_548031277288491442[43] = 0;
   out_548031277288491442[44] = 0;
   out_548031277288491442[45] = dt*(stiffness_front*(-state[2] - state[3] + state[7])/(mass*state[1]) + (-stiffness_front - stiffness_rear)*state[5]/(mass*state[4]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[6]/(mass*state[4]));
   out_548031277288491442[46] = -dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*pow(state[1], 2));
   out_548031277288491442[47] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_548031277288491442[48] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_548031277288491442[49] = dt*((-1 - (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*pow(state[4], 2)))*state[6] - (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*pow(state[4], 2)));
   out_548031277288491442[50] = dt*(-stiffness_front*state[0] - stiffness_rear*state[0])/(mass*state[4]) + 1;
   out_548031277288491442[51] = dt*(-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]));
   out_548031277288491442[52] = dt*stiffness_front*state[0]/(mass*state[1]);
   out_548031277288491442[53] = -9.8100000000000005*dt;
   out_548031277288491442[54] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front - pow(center_to_rear, 2)*stiffness_rear)*state[6]/(rotational_inertia*state[4]));
   out_548031277288491442[55] = -center_to_front*dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*pow(state[1], 2));
   out_548031277288491442[56] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_548031277288491442[57] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_548031277288491442[58] = dt*(-(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*pow(state[4], 2)) - (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*pow(state[4], 2)));
   out_548031277288491442[59] = dt*(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(rotational_inertia*state[4]);
   out_548031277288491442[60] = dt*(-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])/(rotational_inertia*state[4]) + 1;
   out_548031277288491442[61] = center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_548031277288491442[62] = 0;
   out_548031277288491442[63] = 0;
   out_548031277288491442[64] = 0;
   out_548031277288491442[65] = 0;
   out_548031277288491442[66] = 0;
   out_548031277288491442[67] = 0;
   out_548031277288491442[68] = 0;
   out_548031277288491442[69] = 0;
   out_548031277288491442[70] = 1;
   out_548031277288491442[71] = 0;
   out_548031277288491442[72] = 0;
   out_548031277288491442[73] = 0;
   out_548031277288491442[74] = 0;
   out_548031277288491442[75] = 0;
   out_548031277288491442[76] = 0;
   out_548031277288491442[77] = 0;
   out_548031277288491442[78] = 0;
   out_548031277288491442[79] = 0;
   out_548031277288491442[80] = 1;
}
void h_25(double *state, double *unused, double *out_7825793012049214991) {
   out_7825793012049214991[0] = state[6];
}
void H_25(double *state, double *unused, double *out_8986251996152959258) {
   out_8986251996152959258[0] = 0;
   out_8986251996152959258[1] = 0;
   out_8986251996152959258[2] = 0;
   out_8986251996152959258[3] = 0;
   out_8986251996152959258[4] = 0;
   out_8986251996152959258[5] = 0;
   out_8986251996152959258[6] = 1;
   out_8986251996152959258[7] = 0;
   out_8986251996152959258[8] = 0;
}
void h_24(double *state, double *unused, double *out_3863850671382452828) {
   out_3863850671382452828[0] = state[4];
   out_3863850671382452828[1] = state[5];
}
void H_24(double *state, double *unused, double *out_5428326397350636122) {
   out_5428326397350636122[0] = 0;
   out_5428326397350636122[1] = 0;
   out_5428326397350636122[2] = 0;
   out_5428326397350636122[3] = 0;
   out_5428326397350636122[4] = 1;
   out_5428326397350636122[5] = 0;
   out_5428326397350636122[6] = 0;
   out_5428326397350636122[7] = 0;
   out_5428326397350636122[8] = 0;
   out_5428326397350636122[9] = 0;
   out_5428326397350636122[10] = 0;
   out_5428326397350636122[11] = 0;
   out_5428326397350636122[12] = 0;
   out_5428326397350636122[13] = 0;
   out_5428326397350636122[14] = 1;
   out_5428326397350636122[15] = 0;
   out_5428326397350636122[16] = 0;
   out_5428326397350636122[17] = 0;
}
void h_30(double *state, double *unused, double *out_3653635896979304131) {
   out_3653635896979304131[0] = state[4];
}
void H_30(double *state, double *unused, double *out_6467919037645710631) {
   out_6467919037645710631[0] = 0;
   out_6467919037645710631[1] = 0;
   out_6467919037645710631[2] = 0;
   out_6467919037645710631[3] = 0;
   out_6467919037645710631[4] = 1;
   out_6467919037645710631[5] = 0;
   out_6467919037645710631[6] = 0;
   out_6467919037645710631[7] = 0;
   out_6467919037645710631[8] = 0;
}
void h_26(double *state, double *unused, double *out_8655309164640149776) {
   out_8655309164640149776[0] = state[7];
}
void H_26(double *state, double *unused, double *out_5718988758682536134) {
   out_5718988758682536134[0] = 0;
   out_5718988758682536134[1] = 0;
   out_5718988758682536134[2] = 0;
   out_5718988758682536134[3] = 0;
   out_5718988758682536134[4] = 0;
   out_5718988758682536134[5] = 0;
   out_5718988758682536134[6] = 0;
   out_5718988758682536134[7] = 1;
   out_5718988758682536134[8] = 0;
}
void h_27(double *state, double *unused, double *out_5968325673074535865) {
   out_5968325673074535865[0] = state[3];
}
void H_27(double *state, double *unused, double *out_4244324966461767414) {
   out_4244324966461767414[0] = 0;
   out_4244324966461767414[1] = 0;
   out_4244324966461767414[2] = 0;
   out_4244324966461767414[3] = 1;
   out_4244324966461767414[4] = 0;
   out_4244324966461767414[5] = 0;
   out_4244324966461767414[6] = 0;
   out_4244324966461767414[7] = 0;
   out_4244324966461767414[8] = 0;
}
void h_29(double *state, double *unused, double *out_2671551803644006019) {
   out_2671551803644006019[0] = state[1];
}
void H_29(double *state, double *unused, double *out_5957687693331318447) {
   out_5957687693331318447[0] = 0;
   out_5957687693331318447[1] = 1;
   out_5957687693331318447[2] = 0;
   out_5957687693331318447[3] = 0;
   out_5957687693331318447[4] = 0;
   out_5957687693331318447[5] = 0;
   out_5957687693331318447[6] = 0;
   out_5957687693331318447[7] = 0;
   out_5957687693331318447[8] = 0;
}
void h_28(double *state, double *unused, double *out_3845497107703219871) {
   out_3845497107703219871[0] = state[0];
}
void H_28(double *state, double *unused, double *out_7406657363308702595) {
   out_7406657363308702595[0] = 1;
   out_7406657363308702595[1] = 0;
   out_7406657363308702595[2] = 0;
   out_7406657363308702595[3] = 0;
   out_7406657363308702595[4] = 0;
   out_7406657363308702595[5] = 0;
   out_7406657363308702595[6] = 0;
   out_7406657363308702595[7] = 0;
   out_7406657363308702595[8] = 0;
}
void h_31(double *state, double *unused, double *out_1981073584941794741) {
   out_1981073584941794741[0] = state[8];
}
void H_31(double *state, double *unused, double *out_8955606034275998830) {
   out_8955606034275998830[0] = 0;
   out_8955606034275998830[1] = 0;
   out_8955606034275998830[2] = 0;
   out_8955606034275998830[3] = 0;
   out_8955606034275998830[4] = 0;
   out_8955606034275998830[5] = 0;
   out_8955606034275998830[6] = 0;
   out_8955606034275998830[7] = 0;
   out_8955606034275998830[8] = 1;
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
void car_err_fun(double *nom_x, double *delta_x, double *out_7353202191454035649) {
  err_fun(nom_x, delta_x, out_7353202191454035649);
}
void car_inv_err_fun(double *nom_x, double *true_x, double *out_5273149315250963980) {
  inv_err_fun(nom_x, true_x, out_5273149315250963980);
}
void car_H_mod_fun(double *state, double *out_5463203357507594110) {
  H_mod_fun(state, out_5463203357507594110);
}
void car_f_fun(double *state, double dt, double *out_6125045598488451197) {
  f_fun(state,  dt, out_6125045598488451197);
}
void car_F_fun(double *state, double dt, double *out_548031277288491442) {
  F_fun(state,  dt, out_548031277288491442);
}
void car_h_25(double *state, double *unused, double *out_7825793012049214991) {
  h_25(state, unused, out_7825793012049214991);
}
void car_H_25(double *state, double *unused, double *out_8986251996152959258) {
  H_25(state, unused, out_8986251996152959258);
}
void car_h_24(double *state, double *unused, double *out_3863850671382452828) {
  h_24(state, unused, out_3863850671382452828);
}
void car_H_24(double *state, double *unused, double *out_5428326397350636122) {
  H_24(state, unused, out_5428326397350636122);
}
void car_h_30(double *state, double *unused, double *out_3653635896979304131) {
  h_30(state, unused, out_3653635896979304131);
}
void car_H_30(double *state, double *unused, double *out_6467919037645710631) {
  H_30(state, unused, out_6467919037645710631);
}
void car_h_26(double *state, double *unused, double *out_8655309164640149776) {
  h_26(state, unused, out_8655309164640149776);
}
void car_H_26(double *state, double *unused, double *out_5718988758682536134) {
  H_26(state, unused, out_5718988758682536134);
}
void car_h_27(double *state, double *unused, double *out_5968325673074535865) {
  h_27(state, unused, out_5968325673074535865);
}
void car_H_27(double *state, double *unused, double *out_4244324966461767414) {
  H_27(state, unused, out_4244324966461767414);
}
void car_h_29(double *state, double *unused, double *out_2671551803644006019) {
  h_29(state, unused, out_2671551803644006019);
}
void car_H_29(double *state, double *unused, double *out_5957687693331318447) {
  H_29(state, unused, out_5957687693331318447);
}
void car_h_28(double *state, double *unused, double *out_3845497107703219871) {
  h_28(state, unused, out_3845497107703219871);
}
void car_H_28(double *state, double *unused, double *out_7406657363308702595) {
  H_28(state, unused, out_7406657363308702595);
}
void car_h_31(double *state, double *unused, double *out_1981073584941794741) {
  h_31(state, unused, out_1981073584941794741);
}
void car_H_31(double *state, double *unused, double *out_8955606034275998830) {
  H_31(state, unused, out_8955606034275998830);
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
