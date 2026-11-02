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
void car_err_fun(double *nom_x, double *delta_x, double *out_2869805078350318634);
void car_inv_err_fun(double *nom_x, double *true_x, double *out_6624138795093603288);
void car_H_mod_fun(double *state, double *out_6424885359819808556);
void car_f_fun(double *state, double dt, double *out_3758174016504973643);
void car_F_fun(double *state, double dt, double *out_6192627187967339842);
void car_h_25(double *state, double *unused, double *out_1759823454732362499);
void car_H_25(double *state, double *unused, double *out_2901904709830316879);
void car_h_24(double *state, double *unused, double *out_1305817215653664496);
void car_H_24(double *state, double *unused, double *out_656020888972006257);
void car_h_30(double *state, double *unused, double *out_9220382218981250824);
void car_H_30(double *state, double *unused, double *out_3031243656973556949);
void car_h_26(double *state, double *unused, double *out_2821340701567401678);
void car_H_26(double *state, double *unused, double *out_6643408028704373103);
void car_h_27(double *state, double *unused, double *out_2703333307634024934);
void car_H_27(double *state, double *unused, double *out_5206006968773981860);
void car_h_29(double *state, double *unused, double *out_7103533289356984798);
void car_H_29(double *state, double *unused, double *out_6919369695643532893);
void car_h_28(double *state, double *unused, double *out_5740119359078357619);
void car_H_28(double *state, double *unused, double *out_4955739424078206642);
void car_h_31(double *state, double *unused, double *out_1602636786381233354);
void car_H_31(double *state, double *unused, double *out_2871258747953356451);
void car_predict(double *in_x, double *in_P, double *in_Q, double dt);
void car_set_mass(double x);
void car_set_rotational_inertia(double x);
void car_set_center_to_front(double x);
void car_set_center_to_rear(double x);
void car_set_stiffness_front(double x);
void car_set_stiffness_rear(double x);
}