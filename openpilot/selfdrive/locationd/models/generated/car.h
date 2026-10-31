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
void car_err_fun(double *nom_x, double *delta_x, double *out_5237522992400909671);
void car_inv_err_fun(double *nom_x, double *true_x, double *out_4520032815172558841);
void car_H_mod_fun(double *state, double *out_5454958910934564864);
void car_f_fun(double *state, double dt, double *out_8393141651434012145);
void car_F_fun(double *state, double dt, double *out_3892894923326252036);
void car_h_25(double *state, double *unused, double *out_3399840356834753710);
void car_H_25(double *state, double *unused, double *out_3798596936862749515);
void car_h_24(double *state, double *unused, double *out_7196280050815861091);
void car_H_24(double *state, double *unused, double *out_1625947337857249949);
void car_h_30(double *state, double *unused, double *out_237609171419268640);
void car_H_30(double *state, double *unused, double *out_3669257989719509445);
void car_h_26(double *state, double *unused, double *out_3274395222863601325);
void car_H_26(double *state, double *unused, double *out_57093617988693291);
void car_h_27(double *state, double *unused, double *out_5620703055605722732);
void car_H_27(double *state, double *unused, double *out_1494494677919084534);
void car_h_29(double *state, double *unused, double *out_5463516387254593587);
void car_H_29(double *state, double *unused, double *out_218868048950466499);
void car_h_28(double *state, double *unused, double *out_7380136261180748830);
void car_H_28(double *state, double *unused, double *out_1744762222614859752);
void car_h_31(double *state, double *unused, double *out_4363424899985918731);
void car_H_31(double *state, double *unused, double *out_3829242898739709943);
void car_predict(double *in_x, double *in_P, double *in_Q, double dt);
void car_set_mass(double x);
void car_set_rotational_inertia(double x);
void car_set_center_to_front(double x);
void car_set_center_to_rear(double x);
void car_set_stiffness_front(double x);
void car_set_stiffness_rear(double x);
}