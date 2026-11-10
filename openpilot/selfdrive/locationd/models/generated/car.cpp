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
void err_fun(double *nom_x, double *delta_x, double *out_8620679532312260376) {
   out_8620679532312260376[0] = delta_x[0] + nom_x[0];
   out_8620679532312260376[1] = delta_x[1] + nom_x[1];
   out_8620679532312260376[2] = delta_x[2] + nom_x[2];
   out_8620679532312260376[3] = delta_x[3] + nom_x[3];
   out_8620679532312260376[4] = delta_x[4] + nom_x[4];
   out_8620679532312260376[5] = delta_x[5] + nom_x[5];
   out_8620679532312260376[6] = delta_x[6] + nom_x[6];
   out_8620679532312260376[7] = delta_x[7] + nom_x[7];
   out_8620679532312260376[8] = delta_x[8] + nom_x[8];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_3213444003882710209) {
   out_3213444003882710209[0] = -nom_x[0] + true_x[0];
   out_3213444003882710209[1] = -nom_x[1] + true_x[1];
   out_3213444003882710209[2] = -nom_x[2] + true_x[2];
   out_3213444003882710209[3] = -nom_x[3] + true_x[3];
   out_3213444003882710209[4] = -nom_x[4] + true_x[4];
   out_3213444003882710209[5] = -nom_x[5] + true_x[5];
   out_3213444003882710209[6] = -nom_x[6] + true_x[6];
   out_3213444003882710209[7] = -nom_x[7] + true_x[7];
   out_3213444003882710209[8] = -nom_x[8] + true_x[8];
}
void H_mod_fun(double *state, double *out_7129188878435715543) {
   out_7129188878435715543[0] = 1.0;
   out_7129188878435715543[1] = 0.0;
   out_7129188878435715543[2] = 0.0;
   out_7129188878435715543[3] = 0.0;
   out_7129188878435715543[4] = 0.0;
   out_7129188878435715543[5] = 0.0;
   out_7129188878435715543[6] = 0.0;
   out_7129188878435715543[7] = 0.0;
   out_7129188878435715543[8] = 0.0;
   out_7129188878435715543[9] = 0.0;
   out_7129188878435715543[10] = 1.0;
   out_7129188878435715543[11] = 0.0;
   out_7129188878435715543[12] = 0.0;
   out_7129188878435715543[13] = 0.0;
   out_7129188878435715543[14] = 0.0;
   out_7129188878435715543[15] = 0.0;
   out_7129188878435715543[16] = 0.0;
   out_7129188878435715543[17] = 0.0;
   out_7129188878435715543[18] = 0.0;
   out_7129188878435715543[19] = 0.0;
   out_7129188878435715543[20] = 1.0;
   out_7129188878435715543[21] = 0.0;
   out_7129188878435715543[22] = 0.0;
   out_7129188878435715543[23] = 0.0;
   out_7129188878435715543[24] = 0.0;
   out_7129188878435715543[25] = 0.0;
   out_7129188878435715543[26] = 0.0;
   out_7129188878435715543[27] = 0.0;
   out_7129188878435715543[28] = 0.0;
   out_7129188878435715543[29] = 0.0;
   out_7129188878435715543[30] = 1.0;
   out_7129188878435715543[31] = 0.0;
   out_7129188878435715543[32] = 0.0;
   out_7129188878435715543[33] = 0.0;
   out_7129188878435715543[34] = 0.0;
   out_7129188878435715543[35] = 0.0;
   out_7129188878435715543[36] = 0.0;
   out_7129188878435715543[37] = 0.0;
   out_7129188878435715543[38] = 0.0;
   out_7129188878435715543[39] = 0.0;
   out_7129188878435715543[40] = 1.0;
   out_7129188878435715543[41] = 0.0;
   out_7129188878435715543[42] = 0.0;
   out_7129188878435715543[43] = 0.0;
   out_7129188878435715543[44] = 0.0;
   out_7129188878435715543[45] = 0.0;
   out_7129188878435715543[46] = 0.0;
   out_7129188878435715543[47] = 0.0;
   out_7129188878435715543[48] = 0.0;
   out_7129188878435715543[49] = 0.0;
   out_7129188878435715543[50] = 1.0;
   out_7129188878435715543[51] = 0.0;
   out_7129188878435715543[52] = 0.0;
   out_7129188878435715543[53] = 0.0;
   out_7129188878435715543[54] = 0.0;
   out_7129188878435715543[55] = 0.0;
   out_7129188878435715543[56] = 0.0;
   out_7129188878435715543[57] = 0.0;
   out_7129188878435715543[58] = 0.0;
   out_7129188878435715543[59] = 0.0;
   out_7129188878435715543[60] = 1.0;
   out_7129188878435715543[61] = 0.0;
   out_7129188878435715543[62] = 0.0;
   out_7129188878435715543[63] = 0.0;
   out_7129188878435715543[64] = 0.0;
   out_7129188878435715543[65] = 0.0;
   out_7129188878435715543[66] = 0.0;
   out_7129188878435715543[67] = 0.0;
   out_7129188878435715543[68] = 0.0;
   out_7129188878435715543[69] = 0.0;
   out_7129188878435715543[70] = 1.0;
   out_7129188878435715543[71] = 0.0;
   out_7129188878435715543[72] = 0.0;
   out_7129188878435715543[73] = 0.0;
   out_7129188878435715543[74] = 0.0;
   out_7129188878435715543[75] = 0.0;
   out_7129188878435715543[76] = 0.0;
   out_7129188878435715543[77] = 0.0;
   out_7129188878435715543[78] = 0.0;
   out_7129188878435715543[79] = 0.0;
   out_7129188878435715543[80] = 1.0;
}
void f_fun(double *state, double dt, double *out_1600104142559118766) {
   out_1600104142559118766[0] = state[0];
   out_1600104142559118766[1] = state[1];
   out_1600104142559118766[2] = state[2];
   out_1600104142559118766[3] = state[3];
   out_1600104142559118766[4] = state[4];
   out_1600104142559118766[5] = dt*((-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]))*state[6] - 9.8100000000000005*state[8] + stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*state[1]) + (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*state[4])) + state[5];
   out_1600104142559118766[6] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*state[4])) + state[6];
   out_1600104142559118766[7] = state[7];
   out_1600104142559118766[8] = state[8];
}
void F_fun(double *state, double dt, double *out_5916974821235642797) {
   out_5916974821235642797[0] = 1;
   out_5916974821235642797[1] = 0;
   out_5916974821235642797[2] = 0;
   out_5916974821235642797[3] = 0;
   out_5916974821235642797[4] = 0;
   out_5916974821235642797[5] = 0;
   out_5916974821235642797[6] = 0;
   out_5916974821235642797[7] = 0;
   out_5916974821235642797[8] = 0;
   out_5916974821235642797[9] = 0;
   out_5916974821235642797[10] = 1;
   out_5916974821235642797[11] = 0;
   out_5916974821235642797[12] = 0;
   out_5916974821235642797[13] = 0;
   out_5916974821235642797[14] = 0;
   out_5916974821235642797[15] = 0;
   out_5916974821235642797[16] = 0;
   out_5916974821235642797[17] = 0;
   out_5916974821235642797[18] = 0;
   out_5916974821235642797[19] = 0;
   out_5916974821235642797[20] = 1;
   out_5916974821235642797[21] = 0;
   out_5916974821235642797[22] = 0;
   out_5916974821235642797[23] = 0;
   out_5916974821235642797[24] = 0;
   out_5916974821235642797[25] = 0;
   out_5916974821235642797[26] = 0;
   out_5916974821235642797[27] = 0;
   out_5916974821235642797[28] = 0;
   out_5916974821235642797[29] = 0;
   out_5916974821235642797[30] = 1;
   out_5916974821235642797[31] = 0;
   out_5916974821235642797[32] = 0;
   out_5916974821235642797[33] = 0;
   out_5916974821235642797[34] = 0;
   out_5916974821235642797[35] = 0;
   out_5916974821235642797[36] = 0;
   out_5916974821235642797[37] = 0;
   out_5916974821235642797[38] = 0;
   out_5916974821235642797[39] = 0;
   out_5916974821235642797[40] = 1;
   out_5916974821235642797[41] = 0;
   out_5916974821235642797[42] = 0;
   out_5916974821235642797[43] = 0;
   out_5916974821235642797[44] = 0;
   out_5916974821235642797[45] = dt*(stiffness_front*(-state[2] - state[3] + state[7])/(mass*state[1]) + (-stiffness_front - stiffness_rear)*state[5]/(mass*state[4]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[6]/(mass*state[4]));
   out_5916974821235642797[46] = -dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(mass*pow(state[1], 2));
   out_5916974821235642797[47] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_5916974821235642797[48] = -dt*stiffness_front*state[0]/(mass*state[1]);
   out_5916974821235642797[49] = dt*((-1 - (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*pow(state[4], 2)))*state[6] - (-stiffness_front*state[0] - stiffness_rear*state[0])*state[5]/(mass*pow(state[4], 2)));
   out_5916974821235642797[50] = dt*(-stiffness_front*state[0] - stiffness_rear*state[0])/(mass*state[4]) + 1;
   out_5916974821235642797[51] = dt*(-state[4] + (-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(mass*state[4]));
   out_5916974821235642797[52] = dt*stiffness_front*state[0]/(mass*state[1]);
   out_5916974821235642797[53] = -9.8100000000000005*dt;
   out_5916974821235642797[54] = dt*(center_to_front*stiffness_front*(-state[2] - state[3] + state[7])/(rotational_inertia*state[1]) + (-center_to_front*stiffness_front + center_to_rear*stiffness_rear)*state[5]/(rotational_inertia*state[4]) + (-pow(center_to_front, 2)*stiffness_front - pow(center_to_rear, 2)*stiffness_rear)*state[6]/(rotational_inertia*state[4]));
   out_5916974821235642797[55] = -center_to_front*dt*stiffness_front*(-state[2] - state[3] + state[7])*state[0]/(rotational_inertia*pow(state[1], 2));
   out_5916974821235642797[56] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_5916974821235642797[57] = -center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_5916974821235642797[58] = dt*(-(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])*state[5]/(rotational_inertia*pow(state[4], 2)) - (-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])*state[6]/(rotational_inertia*pow(state[4], 2)));
   out_5916974821235642797[59] = dt*(-center_to_front*stiffness_front*state[0] + center_to_rear*stiffness_rear*state[0])/(rotational_inertia*state[4]);
   out_5916974821235642797[60] = dt*(-pow(center_to_front, 2)*stiffness_front*state[0] - pow(center_to_rear, 2)*stiffness_rear*state[0])/(rotational_inertia*state[4]) + 1;
   out_5916974821235642797[61] = center_to_front*dt*stiffness_front*state[0]/(rotational_inertia*state[1]);
   out_5916974821235642797[62] = 0;
   out_5916974821235642797[63] = 0;
   out_5916974821235642797[64] = 0;
   out_5916974821235642797[65] = 0;
   out_5916974821235642797[66] = 0;
   out_5916974821235642797[67] = 0;
   out_5916974821235642797[68] = 0;
   out_5916974821235642797[69] = 0;
   out_5916974821235642797[70] = 1;
   out_5916974821235642797[71] = 0;
   out_5916974821235642797[72] = 0;
   out_5916974821235642797[73] = 0;
   out_5916974821235642797[74] = 0;
   out_5916974821235642797[75] = 0;
   out_5916974821235642797[76] = 0;
   out_5916974821235642797[77] = 0;
   out_5916974821235642797[78] = 0;
   out_5916974821235642797[79] = 0;
   out_5916974821235642797[80] = 1;
}
void h_25(double *state, double *unused, double *out_6493586036867506847) {
   out_6493586036867506847[0] = state[6];
}
void H_25(double *state, double *unused, double *out_7634523320636012255) {
   out_7634523320636012255[0] = 0;
   out_7634523320636012255[1] = 0;
   out_7634523320636012255[2] = 0;
   out_7634523320636012255[3] = 0;
   out_7634523320636012255[4] = 0;
   out_7634523320636012255[5] = 0;
   out_7634523320636012255[6] = 1;
   out_7634523320636012255[7] = 0;
   out_7634523320636012255[8] = 0;
}
void h_24(double *state, double *unused, double *out_3939890314802909583) {
   out_3939890314802909583[0] = state[4];
   out_3939890314802909583[1] = state[5];
}
void H_24(double *state, double *unused, double *out_1433490640784850829) {
   out_1433490640784850829[0] = 0;
   out_1433490640784850829[1] = 0;
   out_1433490640784850829[2] = 0;
   out_1433490640784850829[3] = 0;
   out_1433490640784850829[4] = 1;
   out_1433490640784850829[5] = 0;
   out_1433490640784850829[6] = 0;
   out_1433490640784850829[7] = 0;
   out_1433490640784850829[8] = 0;
   out_1433490640784850829[9] = 0;
   out_1433490640784850829[10] = 0;
   out_1433490640784850829[11] = 0;
   out_1433490640784850829[12] = 0;
   out_1433490640784850829[13] = 0;
   out_1433490640784850829[14] = 1;
   out_1433490640784850829[15] = 0;
   out_1433490640784850829[16] = 0;
   out_1433490640784850829[17] = 0;
}
void h_30(double *state, double *unused, double *out_7458606308908419856) {
   out_7458606308908419856[0] = state[4];
}
void H_30(double *state, double *unused, double *out_3895530411581922606) {
   out_3895530411581922606[0] = 0;
   out_3895530411581922606[1] = 0;
   out_3895530411581922606[2] = 0;
   out_3895530411581922606[3] = 0;
   out_3895530411581922606[4] = 1;
   out_3895530411581922606[5] = 0;
   out_3895530411581922606[6] = 0;
   out_3895530411581922606[7] = 0;
   out_3895530411581922606[8] = 0;
}
void h_26(double *state, double *unused, double *out_7712289952053675171) {
   out_7712289952053675171[0] = state[7];
}
void H_26(double *state, double *unused, double *out_3893020001761956031) {
   out_3893020001761956031[0] = 0;
   out_3893020001761956031[1] = 0;
   out_3893020001761956031[2] = 0;
   out_3893020001761956031[3] = 0;
   out_3893020001761956031[4] = 0;
   out_3893020001761956031[5] = 0;
   out_3893020001761956031[6] = 0;
   out_3893020001761956031[7] = 1;
   out_3893020001761956031[8] = 0;
}
void h_27(double *state, double *unused, double *out_6951484769181199124) {
   out_6951484769181199124[0] = state[3];
}
void H_27(double *state, double *unused, double *out_5330421061692347274) {
   out_5330421061692347274[0] = 0;
   out_5330421061692347274[1] = 0;
   out_5330421061692347274[2] = 0;
   out_5330421061692347274[3] = 1;
   out_5330421061692347274[4] = 0;
   out_5330421061692347274[5] = 0;
   out_5330421061692347274[6] = 0;
   out_5330421061692347274[7] = 0;
   out_5330421061692347274[8] = 0;
}
void h_29(double *state, double *unused, double *out_3731994047707680051) {
   out_3731994047707680051[0] = state[1];
}
void H_29(double *state, double *unused, double *out_8015415717807164369) {
   out_8015415717807164369[0] = 0;
   out_8015415717807164369[1] = 1;
   out_8015415717807164369[2] = 0;
   out_8015415717807164369[3] = 0;
   out_8015415717807164369[4] = 0;
   out_8015415717807164369[5] = 0;
   out_8015415717807164369[6] = 0;
   out_8015415717807164369[7] = 0;
   out_8015415717807164369[8] = 0;
}
void h_28(double *state, double *unused, double *out_4060115870099651679) {
   out_4060115870099651679[0] = state[0];
}
void H_28(double *state, double *unused, double *out_8467698084337060996) {
   out_8467698084337060996[0] = 1;
   out_8467698084337060996[1] = 0;
   out_8467698084337060996[2] = 0;
   out_8467698084337060996[3] = 0;
   out_8467698084337060996[4] = 0;
   out_8467698084337060996[5] = 0;
   out_8467698084337060996[6] = 0;
   out_8467698084337060996[7] = 0;
   out_8467698084337060996[8] = 0;
}
void h_31(double *state, double *unused, double *out_5182322810491693833) {
   out_5182322810491693833[0] = state[8];
}
void H_31(double *state, double *unused, double *out_7665169282512972683) {
   out_7665169282512972683[0] = 0;
   out_7665169282512972683[1] = 0;
   out_7665169282512972683[2] = 0;
   out_7665169282512972683[3] = 0;
   out_7665169282512972683[4] = 0;
   out_7665169282512972683[5] = 0;
   out_7665169282512972683[6] = 0;
   out_7665169282512972683[7] = 0;
   out_7665169282512972683[8] = 1;
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
void car_err_fun(double *nom_x, double *delta_x, double *out_8620679532312260376) {
  err_fun(nom_x, delta_x, out_8620679532312260376);
}
void car_inv_err_fun(double *nom_x, double *true_x, double *out_3213444003882710209) {
  inv_err_fun(nom_x, true_x, out_3213444003882710209);
}
void car_H_mod_fun(double *state, double *out_7129188878435715543) {
  H_mod_fun(state, out_7129188878435715543);
}
void car_f_fun(double *state, double dt, double *out_1600104142559118766) {
  f_fun(state,  dt, out_1600104142559118766);
}
void car_F_fun(double *state, double dt, double *out_5916974821235642797) {
  F_fun(state,  dt, out_5916974821235642797);
}
void car_h_25(double *state, double *unused, double *out_6493586036867506847) {
  h_25(state, unused, out_6493586036867506847);
}
void car_H_25(double *state, double *unused, double *out_7634523320636012255) {
  H_25(state, unused, out_7634523320636012255);
}
void car_h_24(double *state, double *unused, double *out_3939890314802909583) {
  h_24(state, unused, out_3939890314802909583);
}
void car_H_24(double *state, double *unused, double *out_1433490640784850829) {
  H_24(state, unused, out_1433490640784850829);
}
void car_h_30(double *state, double *unused, double *out_7458606308908419856) {
  h_30(state, unused, out_7458606308908419856);
}
void car_H_30(double *state, double *unused, double *out_3895530411581922606) {
  H_30(state, unused, out_3895530411581922606);
}
void car_h_26(double *state, double *unused, double *out_7712289952053675171) {
  h_26(state, unused, out_7712289952053675171);
}
void car_H_26(double *state, double *unused, double *out_3893020001761956031) {
  H_26(state, unused, out_3893020001761956031);
}
void car_h_27(double *state, double *unused, double *out_6951484769181199124) {
  h_27(state, unused, out_6951484769181199124);
}
void car_H_27(double *state, double *unused, double *out_5330421061692347274) {
  H_27(state, unused, out_5330421061692347274);
}
void car_h_29(double *state, double *unused, double *out_3731994047707680051) {
  h_29(state, unused, out_3731994047707680051);
}
void car_H_29(double *state, double *unused, double *out_8015415717807164369) {
  H_29(state, unused, out_8015415717807164369);
}
void car_h_28(double *state, double *unused, double *out_4060115870099651679) {
  h_28(state, unused, out_4060115870099651679);
}
void car_H_28(double *state, double *unused, double *out_8467698084337060996) {
  H_28(state, unused, out_8467698084337060996);
}
void car_h_31(double *state, double *unused, double *out_5182322810491693833) {
  h_31(state, unused, out_5182322810491693833);
}
void car_H_31(double *state, double *unused, double *out_7665169282512972683) {
  H_31(state, unused, out_7665169282512972683);
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
