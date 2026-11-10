#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void pose_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_err_fun(double *nom_x, double *delta_x, double *out_5912021845748559900);
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_8843896357291937735);
void pose_H_mod_fun(double *state, double *out_2688320196777535589);
void pose_f_fun(double *state, double dt, double *out_650263676124789422);
void pose_F_fun(double *state, double dt, double *out_7315646842237993430);
void pose_h_4(double *state, double *unused, double *out_2905496685476842828);
void pose_H_4(double *state, double *unused, double *out_5413221642194876961);
void pose_h_10(double *state, double *unused, double *out_1191620274986758585);
void pose_H_10(double *state, double *unused, double *out_1027777997724036985);
void pose_h_13(double *state, double *unused, double *out_7783237198684558375);
void pose_H_13(double *state, double *unused, double *out_2200947816862544160);
void pose_h_14(double *state, double *unused, double *out_1027602586512413166);
void pose_H_14(double *state, double *unused, double *out_1449980785855392432);
void pose_predict(double *in_x, double *in_P, double *in_Q, double dt);
}