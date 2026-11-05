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
void car_err_fun(double *nom_x, double *delta_x, double *out_999653426340036453);
void car_inv_err_fun(double *nom_x, double *true_x, double *out_4610969769563887824);
void car_H_mod_fun(double *state, double *out_1018390387391438144);
void car_f_fun(double *state, double dt, double *out_8437871470129484601);
void car_F_fun(double *state, double dt, double *out_7222922179546997313);
void car_h_25(double *state, double *unused, double *out_5845994727189855200);
void car_H_25(double *state, double *unused, double *out_1618166537623988050);
void car_h_24(double *state, double *unused, double *out_2733857150290724482);
void car_H_24(double *state, double *unused, double *out_3790816136629487616);
void car_h_30(double *state, double *unused, double *out_2208545198935832850);
void car_H_30(double *state, double *unused, double *out_6145862867751596248);
void car_h_26(double *state, double *unused, double *out_604006904334020533);
void car_H_26(double *state, double *unused, double *out_5359669856498044274);
void car_h_27(double *state, double *unused, double *out_844321459529009555);
void car_H_27(double *state, double *unused, double *out_8320626179552021159);
void car_h_29(double *state, double *unused, double *out_2793128068725012795);
void car_H_29(double *state, double *unused, double *out_5635631523437204064);
void car_h_28(double *state, double *unused, double *out_4484828697352558873);
void car_H_28(double *state, double *unused, double *out_3672001251871877813);
void car_h_31(double *state, double *unused, double *out_2046944767694668159);
void car_H_31(double *state, double *unused, double *out_5985877958731395750);
void car_predict(double *in_x, double *in_P, double *in_Q, double dt);
void car_set_mass(double x);
void car_set_rotational_inertia(double x);
void car_set_center_to_front(double x);
void car_set_center_to_rear(double x);
void car_set_stiffness_front(double x);
void car_set_stiffness_rear(double x);
}