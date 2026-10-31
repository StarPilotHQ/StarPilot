#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void pose_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_err_fun(double *nom_x, double *delta_x, double *out_7865313754094985036);
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_9215330386866768578);
void pose_H_mod_fun(double *state, double *out_1168964832405445386);
void pose_f_fun(double *state, double dt, double *out_1695785533815148768);
void pose_F_fun(double *state, double dt, double *out_9012344706101798543);
void pose_h_4(double *state, double *unused, double *out_6129393296503332397);
void pose_H_4(double *state, double *unused, double *out_2031445068696024638);
void pose_h_10(double *state, double *unused, double *out_3045043867218109602);
void pose_H_10(double *state, double *unused, double *out_2178011878899672658);
void pose_h_13(double *state, double *unused, double *out_516116815365053521);
void pose_H_13(double *state, double *unused, double *out_5579186139620676291);
void pose_h_14(double *state, double *unused, double *out_327806075788285461);
void pose_H_14(double *state, double *unused, double *out_5114233500991396934);
void pose_predict(double *in_x, double *in_P, double *in_Q, double dt);
}