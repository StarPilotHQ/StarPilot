#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void pose_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_err_fun(double *nom_x, double *delta_x, double *out_2134324718959906218);
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_7874553077816062449);
void pose_H_mod_fun(double *state, double *out_8129875391940694982);
void pose_f_fun(double *state, double dt, double *out_5920058179701921818);
void pose_F_fun(double *state, double dt, double *out_534012189001621552);
void pose_h_4(double *state, double *unused, double *out_6191552396506608484);
void pose_H_4(double *state, double *unused, double *out_4468786875016897913);
void pose_h_10(double *state, double *unused, double *out_8347226084972414573);
void pose_H_10(double *state, double *unused, double *out_4111169058602364742);
void pose_h_13(double *state, double *unused, double *out_2707578914264125574);
void pose_H_13(double *state, double *unused, double *out_7681060700349230714);
void pose_h_14(double *state, double *unused, double *out_4697260034120461682);
void pose_H_14(double *state, double *unused, double *out_8432027731356382442);
void pose_predict(double *in_x, double *in_P, double *in_Q, double dt);
}