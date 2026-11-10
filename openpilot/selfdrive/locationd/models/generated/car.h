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
void car_err_fun(double *nom_x, double *delta_x, double *out_1014334527327209848);
void car_inv_err_fun(double *nom_x, double *true_x, double *out_3206419511188405682);
void car_H_mod_fun(double *state, double *out_992849709061666640);
void car_f_fun(double *state, double dt, double *out_7284810420003339157);
void car_F_fun(double *state, double dt, double *out_8279640069865100833);
void car_h_25(double *state, double *unused, double *out_8642135170742394965);
void car_H_25(double *state, double *unused, double *out_8200338516850570597);
void car_h_24(double *state, double *unused, double *out_9197263242363141624);
void car_H_24(double *state, double *unused, double *out_1027726669218624628);
void car_h_30(double *state, double *unused, double *out_8720849294696219296);
void car_H_30(double *state, double *unused, double *out_5718709226731372821);
void car_h_26(double *state, double *unused, double *out_4724221511352486675);
void car_H_26(double *state, double *unused, double *out_6504902237984924795);
void car_h_27(double *state, double *unused, double *out_2893523921247530795);
void car_H_27(double *state, double *unused, double *out_7942303297915316038);
void car_h_29(double *state, double *unused, double *out_2618329858963024906);
void car_H_29(double *state, double *unused, double *out_6228940571045765005);
void car_h_28(double *state, double *unused, double *out_8550457448668955042);
void car_H_28(double *state, double *unused, double *out_1146541553976234431);
void car_h_31(double *state, double *unused, double *out_7603820894445690215);
void car_H_31(double *state, double *unused, double *out_8169692554973610169);
void car_predict(double *in_x, double *in_P, double *in_Q, double dt);
void car_set_mass(double x);
void car_set_rotational_inertia(double x);
void car_set_center_to_front(double x);
void car_set_center_to_rear(double x);
void car_set_stiffness_front(double x);
void car_set_stiffness_rear(double x);
}