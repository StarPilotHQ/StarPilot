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
void err_fun(double *nom_x, double *delta_x, double *out_2171972427265756634) {
   out_2171972427265756634[0] = delta_x[0] + nom_x[0];
   out_2171972427265756634[1] = delta_x[1] + nom_x[1];
   out_2171972427265756634[2] = delta_x[2] + nom_x[2];
   out_2171972427265756634[3] = delta_x[3] + nom_x[3];
   out_2171972427265756634[4] = delta_x[4] + nom_x[4];
   out_2171972427265756634[5] = delta_x[5] + nom_x[5];
   out_2171972427265756634[6] = delta_x[6] + nom_x[6];
   out_2171972427265756634[7] = delta_x[7] + nom_x[7];
   out_2171972427265756634[8] = delta_x[8] + nom_x[8];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_6634914969348904448) {
   out_6634914969348904448[0] = -nom_x[0] + true_x[0];
   out_6634914969348904448[1] = -nom_x[1] + true_x[1];
   out_6634914969348904448[2] = -nom_x[2] + true_x[2];
   out_6634914969348904448[3] = -nom_x[3] + true_x[3];
   out_6634914969348904448[4] = -nom_x[4] + true_x[4];
   out_6634914969348904448[5] = -nom_x[5] + true_x[5];
   out_6634914969348904448[6] = -nom_x[6] + true_x[6];
   out_6634914969348904448[7] = -nom_x[7] + true_x[7];
   out_6634914969348904448[8] = -nom_x[8] + true_x[8];
}
void H_mod_fun(double *state, double *out_2620521796582229957) {
   out_2620521796582229957[0] = 1.0;
   out_2620521796582229957[1] = 0.0;
   out_2620521796582229957[2] = 0.0;
   out_2620521796582229957[3] = 0.0;
   out_2620521796582229957[4] = 0.0;
   out_2620521796582229957[5] = 0.0;
   out_2620521796582229957[6] = 0.0;
   out_2620521796582229957[7] = 0.0;
   out_2620521796582229957[8] = 0.0;
   out_2620521796582229957[9] = 0.0;
   out_2620521796582229957[10] = 1.0;
   out_2620521796582229957[11] = 0.0;
   out_2620521796582229957[12] = 0.0;
   out_2620521796582229957[13] = 0.0;
   out_2620521796582229957[14] = 0.0;
   out_2620521796582229957[15] = 0.0;
   out_2620521796582229957[16] = 0.0;
   out_2620521796582229957[17] = 0.0;
   out_2620521796582229957[18] = 0.0;
   out_2620521796582229957[19] = 0.0;
   out_2620521796582229957[20] = 1.0;
   out_2620521796582229957[21] = 0.0;
   out_2620521796582229957[22] = 0.0;
   out_2620521796582229957[23] = 0.0;
   out_2620521796582229957[24] = 0.0;
   out_2620521796582229957[25] = 0.0;
   out_2620521796582229957[26] = 0.0;
   out_2620521796582229957[27] = 0.0;
   out_2620521796582229957[28] = 0.0;
   out_2620521796582229957[29] = 0.0;
   out_2620521796582229957[30] = 1.0;
   out_2620521796582229957[31] = 0.0;
   out_2620521796582229957[32] = 0.0;
   out_2620521796582229957[33] = 0.0;
   out_2620521796582229957[34] = 0.0;
   out_2620521796582229957[35] = 0.0;
   out_2620521796582229957[36] = 0.0;
   out_2620521796582229957[37] = 0.0;
   out_2620521796582229957[38] = 0.0;
   out_2620521796582229957[39] = 0.0;
   out_2620521796582229957[40] = 1.0;
   out_2620521796582229957[41] = 0.0;
   out_2620521796582229957[42] = 0.0;
   out_2620521796582229957[43] = 0.0;
   out_2620521796582229957[44] = 0.0;
   out_2620521796582229957[45] = 0.0;
   out_2620521796582229957[46] = 0.0;
   out_2620521796582229957[47] = 0.0;
   out_2620521796582229957[48] = 0.0;
   out_2620521796582229957[49] = 0.0;
   out_2620521796582229957[50] = 1.0;
   out_2620521796582229957[51] = 0.0;
   out_2620521796582229957[52] = 0.0;
   out_2620521796582229957[53] = 0.0;
   out_2620521796582229957[54] = 0.0;
   out_2620521796582229957[55] = 0.0;
   out_2620521796582229957[56] = 0.0;
   out_2620521796582229957[57] = 0.0;
   out_2620521796582229957[58] = 0.0;
   out_2620521796582229957[59] = 0.0;
   out_2620521796582229957[60] = 1.0;
   out_2620521796582229957[61] = 0.0;
   out_2620521796582229957[62] = 0.0;
   out_2620521796582229957[63] = 0.0;
   out_2620521796582229957[64] = 0.0;
   out_2620521796582229957[65] = 0.0;
   out_2620521796582229957[66] = 0.0;
   out_2620521796582229957[67] = 0.0;
   out_2620521796582229957[68] = 0.0;
   out_2620521796582229957[69] = 0.0;
   out_2620521796582229957[70] = 1.0;
   out_2620521796582229957[71] = 0.0;
   out_2620521796582229957[72] = 0.0;
   out_2620521796582229957[73] = 0.0;
   out_2620521796582229957[74] = 0.0;
   out_2620521796582229957[75] = 0.0;
   out_2620521796582229957[76] = 0.0;
   out_2620521796582229957[77] = 0.0;
   out_2620521796582229957[78] = 0.0;
   out_2620521796582229957[79] = 0.0;
   out_2620521796582229957[80] = 1.0;
}
void f_fun(double *state, double dt, double *out_9021954438602515933) {
   out_9021954438602515933[0] = state[0];
   out_9021954438602515933[1] = state[1];
   out_9021954438602515933[2] = state[2];
   out_9021954438602515933[3] = state[3];
   out_9021954438602515933[4] = state[4];
   out_9021954438602515933[5] = dt*((-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]))*state[6] - 9.8100000000000005*state[8] + stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*state[1]) + (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*state[4])) + state[5];
   out_9021954438602515933[6] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*state[4])) + state[6];
   out_9021954438602515933[7] = state[7];
   out_9021954438602515933[8] = state[8];
}
void F_fun(double *state, double dt, double *out_1280221251351814112) {
   out_1280221251351814112[0] = 1;
   out_1280221251351814112[1] = 0;
   out_1280221251351814112[2] = 0;
   out_1280221251351814112[3] = 0;
   out_1280221251351814112[4] = 0;
   out_1280221251351814112[5] = 0;
   out_1280221251351814112[6] = 0;
   out_1280221251351814112[7] = 0;
   out_1280221251351814112[8] = 0;
   out_1280221251351814112[9] = 0;
   out_1280221251351814112[10] = 1;
   out_1280221251351814112[11] = 0;
   out_1280221251351814112[12] = 0;
   out_1280221251351814112[13] = 0;
   out_1280221251351814112[14] = 0;
   out_1280221251351814112[15] = 0;
   out_1280221251351814112[16] = 0;
   out_1280221251351814112[17] = 0;
   out_1280221251351814112[18] = 0;
   out_1280221251351814112[19] = 0;
   out_1280221251351814112[20] = 1;
   out_1280221251351814112[21] = 0;
   out_1280221251351814112[22] = 0;
   out_1280221251351814112[23] = 0;
   out_1280221251351814112[24] = 0;
   out_1280221251351814112[25] = 0;
   out_1280221251351814112[26] = 0;
   out_1280221251351814112[27] = 0;
   out_1280221251351814112[28] = 0;
   out_1280221251351814112[29] = 0;
   out_1280221251351814112[30] = 1;
   out_1280221251351814112[31] = 0;
   out_1280221251351814112[32] = 0;
   out_1280221251351814112[33] = 0;
   out_1280221251351814112[34] = 0;
   out_1280221251351814112[35] = 0;
   out_1280221251351814112[36] = 0;
   out_1280221251351814112[37] = 0;
   out_1280221251351814112[38] = 0;
   out_1280221251351814112[39] = 0;
   out_1280221251351814112[40] = 1;
   out_1280221251351814112[41] = 0;
   out_1280221251351814112[42] = 0;
   out_1280221251351814112[43] = 0;
   out_1280221251351814112[44] = 0;
   out_1280221251351814112[45] = dt*(stiffness_front*(-state[2] - state[3] + state[7])/(mass*state[1]) + (-stiffness_front - stiffness_rear)*state[5]/(mass*state[4]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[6]/(mass*state[4]));
   out_1280221251351814112[46] = -dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*pow(state[1], 2));
   out_1280221251351814112[47] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_1280221251351814112[48] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_1280221251351814112[49] = dt*((-1 - (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*pow(state[4], 2)))*state[6] - (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*pow(state[4], 2)));
   out_1280221251351814112[50] = dt*(-stiffness_front*state[0] - stiffness_rear*state[0])/(mass*state[4]) + 1;
   out_1280221251351814112[51] = dt*(-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]));
   out_1280221251351814112[52] = dt*stiffness_front*state[0]/(mass*state[1]);
   out_1280221251351814112[53] = -9.8100000000000005*dt;
   out_1280221251351814112[54] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front - pow(center_to_rear, 2)*stiffness_rear)*state[6]/(rotational_inertia*state[4]));
   out_1280221251351814112[55] = -center_to_front*dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*pow(state[1], 2));
   out_1280221251351814112[56] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_1280221251351814112[57] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_1280221251351814112[58] = dt*(-(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*pow(state[4], 2)) - (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*pow(state[4], 2)));
   out_1280221251351814112[59] = dt*(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(rotational_inertia*state[4]);
   out_1280221251351814112[60] = dt*(-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])/(rotational_inertia*state[4]) + 1;
   out_1280221251351814112[61] = center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_1280221251351814112[62] = 0;
   out_1280221251351814112[63] = 0;
   out_1280221251351814112[64] = 0;
   out_1280221251351814112[65] = 0;
   out_1280221251351814112[66] = 0;
   out_1280221251351814112[67] = 0;
   out_1280221251351814112[68] = 0;
   out_1280221251351814112[69] = 0;
   out_1280221251351814112[70] = 1;
   out_1280221251351814112[71] = 0;
   out_1280221251351814112[72] = 0;
   out_1280221251351814112[73] = 0;
   out_1280221251351814112[74] = 0;
   out_1280221251351814112[75] = 0;
   out_1280221251351814112[76] = 0;
   out_1280221251351814112[77] = 0;
   out_1280221251351814112[78] = 0;
   out_1280221251351814112[79] = 0;
   out_1280221251351814112[80] = 1;
}
void h_25(double *state, double *unused, double *out_6213416065106593514) {
   out_6213416065106593514[0] = state[6];
}
void H_25(double *state, double *unused, double *out_902458853407261720) {
   out_902458853407261720[0] = 0;
   out_902458853407261720[1] = 0;
   out_902458853407261720[2] = 0;
   out_902458853407261720[3] = 0;
   out_902458853407261720[4] = 0;
   out_902458853407261720[5] = 0;
   out_902458853407261720[6] = 1;
   out_902458853407261720[7] = 0;
   out_902458853407261720[8] = 0;
}
void h_24(double *state, double *unused, double *out_622747324465739350) {
   out_622747324465739350[0] = state[4];
   out_622747324465739350[1] = state[5];
}
void H_24(double *state, double *unused, double *out_7556664311515447477) {
   out_7556664311515447477[0] = 0;
   out_7556664311515447477[1] = 0;
   out_7556664311515447477[2] = 0;
   out_7556664311515447477[3] = 0;
   out_7556664311515447477[4] = 1;
   out_7556664311515447477[5] = 0;
   out_7556664311515447477[6] = 0;
   out_7556664311515447477[7] = 0;
   out_7556664311515447477[8] = 0;
   out_7556664311515447477[9] = 0;
   out_7556664311515447477[10] = 0;
   out_7556664311515447477[11] = 0;
   out_7556664311515447477[12] = 0;
   out_7556664311515447477[13] = 0;
   out_7556664311515447477[14] = 1;
   out_7556664311515447477[15] = 0;
   out_7556664311515447477[16] = 0;
   out_7556664311515447477[17] = 0;
}
void h_30(double *state, double *unused, double *out_8595878480348935752) {
   out_8595878480348935752[0] = state[4];
}
void H_30(double *state, double *unused, double *out_3625237476720346478) {
   out_3625237476720346478[0] = 0;
   out_3625237476720346478[1] = 0;
   out_3625237476720346478[2] = 0;
   out_3625237476720346478[3] = 0;
   out_3625237476720346478[4] = 1;
   out_3625237476720346478[5] = 0;
   out_3625237476720346478[6] = 0;
   out_3625237476720346478[7] = 0;
   out_3625237476720346478[8] = 0;
}
void h_26(double *state, double *unused, double *out_5559092428904603067) {
   out_5559092428904603067[0] = state[7];
}
void H_26(double *state, double *unused, double *out_2839044465466794504) {
   out_2839044465466794504[0] = 0;
   out_2839044465466794504[1] = 0;
   out_2839044465466794504[2] = 0;
   out_2839044465466794504[3] = 0;
   out_2839044465466794504[4] = 0;
   out_2839044465466794504[5] = 0;
   out_2839044465466794504[6] = 0;
   out_2839044465466794504[7] = 1;
   out_2839044465466794504[8] = 0;
}
void h_27(double *state, double *unused, double *out_3437018544336960962) {
   out_3437018544336960962[0] = state[3];
}
void H_27(double *state, double *unused, double *out_1401643405536403261) {
   out_1401643405536403261[0] = 0;
   out_1401643405536403261[1] = 0;
   out_1401643405536403261[2] = 0;
   out_1401643405536403261[3] = 1;
   out_1401643405536403261[4] = 0;
   out_1401643405536403261[5] = 0;
   out_1401643405536403261[6] = 0;
   out_1401643405536403261[7] = 0;
   out_1401643405536403261[8] = 0;
}
void h_29(double *state, double *unused, double *out_3594205212688090107) {
   out_3594205212688090107[0] = state[1];
}
void H_29(double *state, double *unused, double *out_3115006132405954294) {
   out_3115006132405954294[0] = 0;
   out_3115006132405954294[1] = 1;
   out_3115006132405954294[2] = 0;
   out_3115006132405954294[3] = 0;
   out_3115006132405954294[4] = 0;
   out_3115006132405954294[5] = 0;
   out_3115006132405954294[6] = 0;
   out_3115006132405954294[7] = 0;
   out_3115006132405954294[8] = 0;
}
void h_28(double *state, double *unused, double *out_1629398974381381761) {
   out_1629398974381381761[0] = state[0];
}
void H_28(double *state, double *unused, double *out_1151375860840628043) {
   out_1151375860840628043[0] = 1;
   out_1151375860840628043[1] = 0;
   out_1151375860840628043[2] = 0;
   out_1151375860840628043[3] = 0;
   out_1151375860840628043[4] = 0;
   out_1151375860840628043[5] = 0;
   out_1151375860840628043[6] = 0;
   out_1151375860840628043[7] = 0;
   out_1151375860840628043[8] = 0;
}
void h_31(double *state, double *unused, double *out_978133047748918282) {
   out_978133047748918282[0] = state[8];
}
void H_31(double *state, double *unused, double *out_933104815284222148) {
   out_933104815284222148[0] = 0;
   out_933104815284222148[1] = 0;
   out_933104815284222148[2] = 0;
   out_933104815284222148[3] = 0;
   out_933104815284222148[4] = 0;
   out_933104815284222148[5] = 0;
   out_933104815284222148[6] = 0;
   out_933104815284222148[7] = 0;
   out_933104815284222148[8] = 1;
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
void car_err_fun(double *nom_x, double *delta_x, double *out_2171972427265756634) {
  err_fun(nom_x, delta_x, out_2171972427265756634);
}
void car_inv_err_fun(double *nom_x, double *true_x, double *out_6634914969348904448) {
  inv_err_fun(nom_x, true_x, out_6634914969348904448);
}
void car_H_mod_fun(double *state, double *out_2620521796582229957) {
  H_mod_fun(state, out_2620521796582229957);
}
void car_f_fun(double *state, double dt, double *out_9021954438602515933) {
  f_fun(state,  dt, out_9021954438602515933);
}
void car_F_fun(double *state, double dt, double *out_1280221251351814112) {
  F_fun(state,  dt, out_1280221251351814112);
}
void car_h_25(double *state, double *unused, double *out_6213416065106593514) {
  h_25(state, unused, out_6213416065106593514);
}
void car_H_25(double *state, double *unused, double *out_902458853407261720) {
  H_25(state, unused, out_902458853407261720);
}
void car_h_24(double *state, double *unused, double *out_622747324465739350) {
  h_24(state, unused, out_622747324465739350);
}
void car_H_24(double *state, double *unused, double *out_7556664311515447477) {
  H_24(state, unused, out_7556664311515447477);
}
void car_h_30(double *state, double *unused, double *out_8595878480348935752) {
  h_30(state, unused, out_8595878480348935752);
}
void car_H_30(double *state, double *unused, double *out_3625237476720346478) {
  H_30(state, unused, out_3625237476720346478);
}
void car_h_26(double *state, double *unused, double *out_5559092428904603067) {
  h_26(state, unused, out_5559092428904603067);
}
void car_H_26(double *state, double *unused, double *out_2839044465466794504) {
  H_26(state, unused, out_2839044465466794504);
}
void car_h_27(double *state, double *unused, double *out_3437018544336960962) {
  h_27(state, unused, out_3437018544336960962);
}
void car_H_27(double *state, double *unused, double *out_1401643405536403261) {
  H_27(state, unused, out_1401643405536403261);
}
void car_h_29(double *state, double *unused, double *out_3594205212688090107) {
  h_29(state, unused, out_3594205212688090107);
}
void car_H_29(double *state, double *unused, double *out_3115006132405954294) {
  H_29(state, unused, out_3115006132405954294);
}
void car_h_28(double *state, double *unused, double *out_1629398974381381761) {
  h_28(state, unused, out_1629398974381381761);
}
void car_H_28(double *state, double *unused, double *out_1151375860840628043) {
  H_28(state, unused, out_1151375860840628043);
}
void car_h_31(double *state, double *unused, double *out_978133047748918282) {
  h_31(state, unused, out_978133047748918282);
}
void car_H_31(double *state, double *unused, double *out_933104815284222148) {
  H_31(state, unused, out_933104815284222148);
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
