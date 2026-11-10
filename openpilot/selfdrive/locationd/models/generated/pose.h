#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void pose_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_err_fun(double *nom_x, double *delta_x, double *out_8199567924068407102);
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_5268140790071390257);
void pose_H_mod_fun(double *state, double *out_1833265644165750071);
void pose_f_fun(double *state, double dt, double *out_584860365826166685);
void pose_F_fun(double *state, double dt, double *out_4197666271703963695);
void pose_h_4(double *state, double *unused, double *out_2182850352316805883);
void pose_H_4(double *state, double *unused, double *out_2781361392763566293);
void pose_h_10(double *state, double *unused, double *out_5529312285397171132);
void pose_H_10(double *state, double *unused, double *out_8942930049609574379);
void pose_h_13(double *state, double *unused, double *out_2548470441375331951);
void pose_H_13(double *state, double *unused, double *out_5993635218095899094);
void pose_h_14(double *state, double *unused, double *out_2706566606486851807);
void pose_H_14(double *state, double *unused, double *out_6744602249103050822);
void pose_predict(double *in_x, double *in_P, double *in_Q, double dt);
}