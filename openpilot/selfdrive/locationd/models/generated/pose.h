#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void pose_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_err_fun(double *nom_x, double *delta_x, double *out_4327736854274805720);
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_542433210393079092);
void pose_H_mod_fun(double *state, double *out_2780673785235724857);
void pose_f_fun(double *state, double dt, double *out_2987768017781358156);
void pose_F_fun(double *state, double dt, double *out_9215256453153547448);
void pose_h_4(double *state, double *unused, double *out_3412232203917073472);
void pose_H_4(double *state, double *unused, double *out_3534834787290437464);
void pose_h_10(double *state, double *unused, double *out_115890013071339636);
void pose_H_10(double *state, double *unused, double *out_6130958711041943396);
void pose_h_13(double *state, double *unused, double *out_6371690278926638796);
void pose_H_13(double *state, double *unused, double *out_7301278078102413223);
void pose_h_14(double *state, double *unused, double *out_6221009380571624691);
void pose_H_14(double *state, double *unused, double *out_7498075643629921993);
void pose_predict(double *in_x, double *in_P, double *in_Q, double dt);
}