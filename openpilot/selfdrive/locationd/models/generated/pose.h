#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void pose_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_err_fun(double *nom_x, double *delta_x, double *out_4349191364545344677);
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_6050387772030197335);
void pose_H_mod_fun(double *state, double *out_7888777177672614690);
void pose_f_fun(double *state, double dt, double *out_2236332295548027163);
void pose_F_fun(double *state, double dt, double *out_8464137914874413999);
void pose_h_4(double *state, double *unused, double *out_6867211808814574533);
void pose_H_4(double *state, double *unused, double *out_1124207242241069802);
void pose_h_10(double *state, double *unused, double *out_6302404282439004399);
void pose_H_10(double *state, double *unused, double *out_8521792719043079998);
void pose_h_13(double *state, double *unused, double *out_3755491083597854401);
void pose_H_13(double *state, double *unused, double *out_4336481067573402603);
void pose_h_14(double *state, double *unused, double *out_4171129885438629963);
void pose_H_14(double *state, double *unused, double *out_5087448098580554331);
void pose_predict(double *in_x, double *in_P, double *in_Q, double dt);
}