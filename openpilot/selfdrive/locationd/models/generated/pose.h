#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void pose_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_err_fun(double *nom_x, double *delta_x, double *out_124484568216878126);
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_33395899511450204);
void pose_H_mod_fun(double *state, double *out_8589495166796561709);
void pose_f_fun(double *state, double dt, double *out_3036332123814651041);
void pose_F_fun(double *state, double dt, double *out_3224074787490728613);
void pose_h_4(double *state, double *unused, double *out_1881987918564533025);
void pose_H_4(double *state, double *unused, double *out_2928726227222269101);
void pose_h_10(double *state, double *unused, double *out_6980138353716057693);
void pose_H_10(double *state, double *unused, double *out_3120928681113084341);
void pose_h_13(double *state, double *unused, double *out_7194230607607926667);
void pose_H_13(double *state, double *unused, double *out_6762481690524793125);
void pose_h_14(double *state, double *unused, double *out_1478550648350481724);
void pose_H_14(double *state, double *unused, double *out_8036872031207542091);
void pose_predict(double *in_x, double *in_P, double *in_Q, double dt);
}