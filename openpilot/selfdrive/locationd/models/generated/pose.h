#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void pose_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void pose_err_fun(double *nom_x, double *delta_x, double *out_8879109673270157512);
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_341167623382481477);
void pose_H_mod_fun(double *state, double *out_3336617953655085976);
void pose_f_fun(double *state, double dt, double *out_8832833102796243366);
void pose_F_fun(double *state, double dt, double *out_3295185156675476458);
void pose_h_4(double *state, double *unused, double *out_8502160873985878055);
void pose_H_4(double *state, double *unused, double *out_435189256188821596);
void pose_h_10(double *state, double *unused, double *out_9110483139119498421);
void pose_H_10(double *state, double *unused, double *out_2767835144174802438);
void pose_h_13(double *state, double *unused, double *out_4484268190362306043);
void pose_H_13(double *state, double *unused, double *out_3647463081521154397);
void pose_h_14(double *state, double *unused, double *out_378511576534692133);
void pose_H_14(double *state, double *unused, double *out_2647599176106550700);
void pose_predict(double *in_x, double *in_P, double *in_Q, double dt);
}