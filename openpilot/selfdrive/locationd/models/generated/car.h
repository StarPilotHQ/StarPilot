#pragma once
#include "rednose/helpers/ekf.h"
extern "C" {
void car_update_25(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_24(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_30(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_26(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_27(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_29(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_28(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_update_31(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea);
void car_err_fun(double *nom_x, double *delta_x, double *out_6797865572154616298);
void car_inv_err_fun(double *nom_x, double *true_x, double *out_5593486999645366592);
void car_H_mod_fun(double *state, double *out_1621514070430669622);
void car_f_fun(double *state, double dt, double *out_8357351710386191496);
void car_F_fun(double *state, double dt, double *out_2310875689721956683);
void car_h_25(double *state, double *unused, double *out_3740638677979033229);
void car_H_25(double *state, double *unused, double *out_317174960238698002);
void car_h_24(double *state, double *unused, double *out_7552560004953043119);
void car_H_24(double *state, double *unused, double *out_4503146544417290261);
void car_h_30(double *state, double *unused, double *out_5502959985118725288);
void car_H_30(double *state, double *unused, double *out_187836013095457932);
void car_h_26(double *state, double *unused, double *out_8545832007309197316);
void car_H_26(double *state, double *unused, double *out_974029024349009906);
void car_h_27(double *state, double *unused, double *out_5988268475861826061);
void car_H_27(double *state, double *unused, double *out_1986927298704966979);
void car_h_29(double *state, double *unused, double *out_6446469838020387723);
void car_H_29(double *state, double *unused, double *out_698067357409850116);
void car_h_28(double *state, double *unused, double *out_5384952591185348544);
void car_H_28(double *state, double *unused, double *out_2661697628975176367);
void car_h_31(double *state, double *unused, double *out_3226676486701999265);
void car_H_31(double *state, double *unused, double *out_347820922115658430);
void car_predict(double *in_x, double *in_P, double *in_Q, double dt);
void car_set_mass(double x);
void car_set_rotational_inertia(double x);
void car_set_center_to_front(double x);
void car_set_center_to_rear(double x);
void car_set_stiffness_front(double x);
void car_set_stiffness_rear(double x);
}