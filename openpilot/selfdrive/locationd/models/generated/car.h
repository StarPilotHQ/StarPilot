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
void car_err_fun(double *nom_x, double *delta_x, double *out_8620679532312260376);
void car_inv_err_fun(double *nom_x, double *true_x, double *out_3213444003882710209);
void car_H_mod_fun(double *state, double *out_7129188878435715543);
void car_f_fun(double *state, double dt, double *out_1600104142559118766);
void car_F_fun(double *state, double dt, double *out_5916974821235642797);
void car_h_25(double *state, double *unused, double *out_6493586036867506847);
void car_H_25(double *state, double *unused, double *out_7634523320636012255);
void car_h_24(double *state, double *unused, double *out_3939890314802909583);
void car_H_24(double *state, double *unused, double *out_1433490640784850829);
void car_h_30(double *state, double *unused, double *out_7458606308908419856);
void car_H_30(double *state, double *unused, double *out_3895530411581922606);
void car_h_26(double *state, double *unused, double *out_7712289952053675171);
void car_H_26(double *state, double *unused, double *out_3893020001761956031);
void car_h_27(double *state, double *unused, double *out_6951484769181199124);
void car_H_27(double *state, double *unused, double *out_5330421061692347274);
void car_h_29(double *state, double *unused, double *out_3731994047707680051);
void car_H_29(double *state, double *unused, double *out_8015415717807164369);
void car_h_28(double *state, double *unused, double *out_4060115870099651679);
void car_H_28(double *state, double *unused, double *out_8467698084337060996);
void car_h_31(double *state, double *unused, double *out_5182322810491693833);
void car_H_31(double *state, double *unused, double *out_7665169282512972683);
void car_predict(double *in_x, double *in_P, double *in_Q, double dt);
void car_set_mass(double x);
void car_set_rotational_inertia(double x);
void car_set_center_to_front(double x);
void car_set_center_to_rear(double x);
void car_set_stiffness_front(double x);
void car_set_stiffness_rear(double x);
}