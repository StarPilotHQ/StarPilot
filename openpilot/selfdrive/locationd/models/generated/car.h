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
void car_err_fun(double *nom_x, double *delta_x, double *out_3633129164318593501);
void car_inv_err_fun(double *nom_x, double *true_x, double *out_1063393774227067799);
void car_H_mod_fun(double *state, double *out_3027621333334527262);
void car_f_fun(double *state, double dt, double *out_5598721332850320435);
void car_F_fun(double *state, double dt, double *out_886600525908857299);
void car_h_25(double *state, double *unused, double *out_384996120315224441);
void car_H_25(double *state, double *unused, double *out_8739426620538719443);
void car_h_24(double *state, double *unused, double *out_6799290497032479462);
void car_H_24(double *state, double *unused, double *out_7534667854165332607);
void car_h_30(double *state, double *unused, double *out_7352311284996256935);
void car_H_30(double *state, double *unused, double *out_1822736279047102688);
void car_h_26(double *state, double *unused, double *out_2350638017335411290);
void car_H_26(double *state, double *unused, double *out_5965814134296775949);
void car_h_27(double *state, double *unused, double *out_5870828601643822813);
void car_H_27(double *state, double *unused, double *out_3997499590847527599);
void car_h_29(double *state, double *unused, double *out_2010444049110226372);
void car_H_29(double *state, double *unused, double *out_5710862317717078632);
void car_h_28(double *state, double *unused, double *out_869155333982977168);
void car_H_28(double *state, double *unused, double *out_7653482738922942410);
void car_h_31(double *state, double *unused, double *out_9024873597033766325);
void car_H_31(double *state, double *unused, double *out_8708780658661759015);
void car_predict(double *in_x, double *in_P, double *in_Q, double dt);
void car_set_mass(double x);
void car_set_rotational_inertia(double x);
void car_set_center_to_front(double x);
void car_set_center_to_rear(double x);
void car_set_stiffness_front(double x);
void car_set_stiffness_rear(double x);
}