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
void car_err_fun(double *nom_x, double *delta_x, double *out_7353202191454035649);
void car_inv_err_fun(double *nom_x, double *true_x, double *out_5273149315250963980);
void car_H_mod_fun(double *state, double *out_5463203357507594110);
void car_f_fun(double *state, double dt, double *out_6125045598488451197);
void car_F_fun(double *state, double dt, double *out_548031277288491442);
void car_h_25(double *state, double *unused, double *out_7825793012049214991);
void car_H_25(double *state, double *unused, double *out_8986251996152959258);
void car_h_24(double *state, double *unused, double *out_3863850671382452828);
void car_H_24(double *state, double *unused, double *out_5428326397350636122);
void car_h_30(double *state, double *unused, double *out_3653635896979304131);
void car_H_30(double *state, double *unused, double *out_6467919037645710631);
void car_h_26(double *state, double *unused, double *out_8655309164640149776);
void car_H_26(double *state, double *unused, double *out_5718988758682536134);
void car_h_27(double *state, double *unused, double *out_5968325673074535865);
void car_H_27(double *state, double *unused, double *out_4244324966461767414);
void car_h_29(double *state, double *unused, double *out_2671551803644006019);
void car_H_29(double *state, double *unused, double *out_5957687693331318447);
void car_h_28(double *state, double *unused, double *out_3845497107703219871);
void car_H_28(double *state, double *unused, double *out_7406657363308702595);
void car_h_31(double *state, double *unused, double *out_1981073584941794741);
void car_H_31(double *state, double *unused, double *out_8955606034275998830);
void car_predict(double *in_x, double *in_P, double *in_Q, double dt);
void car_set_mass(double x);
void car_set_rotational_inertia(double x);
void car_set_center_to_front(double x);
void car_set_center_to_rear(double x);
void car_set_stiffness_front(double x);
void car_set_stiffness_rear(double x);
}