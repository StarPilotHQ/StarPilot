#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void pose_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_err_fun(double *nom_x, double *delta_x, double *out_944171533359057280);
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_5987343215061063396);
void pose_H_mod_fun(double *state, double *out_922696574028504956);
void pose_f_fun(double *state, double dt, double *out_7271275588779717019);
void pose_F_fun(double *state, double dt, double *out_1488608338000593967);
void pose_h_4(double *state, double *unused, double *out_7790743839692054955);
void pose_H_4(double *state, double *unused, double *out_1533656544988368493);
void pose_h_10(double *state, double *unused, double *out_9133294609098034128);
void pose_H_10(double *state, double *unused, double *out_1083055239647106891);
void pose_h_13(double *state, double *unused, double *out_7261076778239694426);
void pose_H_13(double *state, double *unused, double *out_4745930370320701294);
void pose_h_14(double *state, double *unused, double *out_3309539515902900024);
void pose_H_14(double *state, double *unused, double *out_5496897401327853022);
void pose_predict(double *in_x, double *in_P, double *in_Q, double dt);
}