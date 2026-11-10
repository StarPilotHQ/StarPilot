#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void pose_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_err_fun(double *nom_x, double *delta_x, double *out_2521549048397866319);
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_4368796387448451064);
void pose_H_mod_fun(double *state, double *out_4711938499332200676);
void pose_f_fun(double *state, double dt, double *out_3583623669529018238);
void pose_F_fun(double *state, double dt, double *out_147281941884768427);
void pose_h_4(double *state, double *unused, double *out_8338357592566013610);
void pose_H_4(double *state, double *unused, double *out_940131289488293104);
void pose_h_10(double *state, double *unused, double *out_8345234452777105931);
void pose_H_10(double *state, double *unused, double *out_1248319787069001495);
void pose_h_13(double *state, double *unused, double *out_1047798615184752827);
void pose_H_13(double *state, double *unused, double *out_375529369806449000);
void pose_h_14(double *state, double *unused, double *out_4235679239024063539);
void pose_H_14(double *state, double *unused, double *out_4022919721783665400);
void pose_predict(double *in_x, double *in_P, double *in_Q, double dt);
}