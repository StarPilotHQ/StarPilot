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
void car_err_fun(double *nom_x, double *delta_x, double *out_6317102778004151788);
void car_inv_err_fun(double *nom_x, double *true_x, double *out_7905438631293668286);
void car_H_mod_fun(double *state, double *out_506592663844380792);
void car_f_fun(double *state, double dt, double *out_2942986894476270401);
void car_F_fun(double *state, double dt, double *out_1935296345496298881);
void car_h_25(double *state, double *unused, double *out_3724255488027275150);
void car_H_25(double *state, double *unused, double *out_8257565196214887805);
void car_h_24(double *state, double *unused, double *out_5876561176471029432);
void car_H_24(double *state, double *unused, double *out_8776150629456254974);
void car_h_30(double *state, double *unused, double *out_4915621988691266734);
void car_H_30(double *state, double *unused, double *out_7670845918987415184);
void car_h_26(double *state, double *unused, double *out_8529448817357439237);
void car_H_26(double *state, double *unused, double *out_4516061877340831581);
void car_h_27(double *state, double *unused, double *out_333074405041906056);
void car_H_27(double *state, double *unused, double *out_5447251847803471967);
void car_h_29(double *state, double *unused, double *out_1186897447041927656);
void car_H_29(double *state, double *unused, double *out_7160614574673023000);
void car_h_28(double *state, double *unused, double *out_6091059319035644012);
void car_H_28(double *state, double *unused, double *out_6203730481966998042);
void car_h_31(double *state, double *unused, double *out_3884116280073868534);
void car_H_31(double *state, double *unused, double *out_8288211158091848233);
void car_predict(double *in_x, double *in_P, double *in_Q, double dt);
void car_set_mass(double x);
void car_set_rotational_inertia(double x);
void car_set_center_to_front(double x);
void car_set_center_to_rear(double x);
void car_set_stiffness_front(double x);
void car_set_stiffness_rear(double x);
}