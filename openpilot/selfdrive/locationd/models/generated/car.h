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
void car_err_fun(double *nom_x, double *delta_x, double *out_4741931869403660740);
void car_inv_err_fun(double *nom_x, double *true_x, double *out_2026357602489443023);
void car_H_mod_fun(double *state, double *out_1259167760902589274);
void car_f_fun(double *state, double dt, double *out_7988689886054324519);
void car_F_fun(double *state, double dt, double *out_1354407396311610150);
void car_h_25(double *state, double *unused, double *out_3766785436680432794);
void car_H_25(double *state, double *unused, double *out_1377389164112836920);
void car_h_24(double *state, double *unused, double *out_7833154037772829772);
void car_H_24(double *state, double *unused, double *out_5708312681030779401);
void car_h_30(double *state, double *unused, double *out_7765906615223965994);
void car_H_30(double *state, double *unused, double *out_5539301177378779835);
void car_h_26(double *state, double *unused, double *out_2764233347563120349);
void car_H_26(double *state, double *unused, double *out_5118892482986893144);
void car_h_27(double *state, double *unused, double *out_6547202700037797670);
void car_H_27(double *state, double *unused, double *out_3681491423056501901);
void car_h_29(double *state, double *unused, double *out_5614419939767242899);
void car_H_29(double *state, double *unused, double *out_5394854149926052934);
void car_h_28(double *state, double *unused, double *out_455560003755268109);
void car_H_28(double *state, double *unused, double *out_3431223878360726683);
void car_h_31(double *state, double *unused, double *out_2316154044051549627);
void car_H_31(double *state, double *unused, double *out_1346743202235876492);
void car_predict(double *in_x, double *in_P, double *in_Q, double dt);
void car_set_mass(double x);
void car_set_rotational_inertia(double x);
void car_set_center_to_front(double x);
void car_set_center_to_rear(double x);
void car_set_stiffness_front(double x);
void car_set_stiffness_rear(double x);
}