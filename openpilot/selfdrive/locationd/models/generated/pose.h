#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void pose_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_err_fun(double *nom_x, double *delta_x, double *out_4313072671461028668);
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_5665898331795518232);
void pose_H_mod_fun(double *state, double *out_8115099160002711194);
void pose_f_fun(double *state, double dt, double *out_161625255094512239);
void pose_F_fun(double *state, double dt, double *out_5522126784489152373);
void pose_h_4(double *state, double *unused, double *out_8968127760178561142);
void pose_H_4(double *state, double *unused, double *out_1329011411857332767);
void pose_h_10(double *state, double *unused, double *out_2424973759049590814);
void pose_H_10(double *state, double *unused, double *out_3789013634481263333);
void pose_h_13(double *state, double *unused, double *out_1545401052236172922);
void pose_H_13(double *state, double *unused, double *out_4541285237189665568);
void pose_h_14(double *state, double *unused, double *out_3039948735172272551);
void pose_H_14(double *state, double *unused, double *out_1753777020438039529);
void pose_predict(double *in_x, double *in_P, double *in_Q, double dt);
}