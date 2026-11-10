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
void car_err_fun(double *nom_x, double *delta_x, double *out_2633990546043802948);
void car_inv_err_fun(double *nom_x, double *true_x, double *out_1648181344021920920);
void car_H_mod_fun(double *state, double *out_6471050303960736584);
void car_f_fun(double *state, double dt, double *out_3688269730868187383);
void car_F_fun(double *state, double dt, double *out_9019266380936848145);
void car_h_25(double *state, double *unused, double *out_797744778366010140);
void car_H_25(double *state, double *unused, double *out_1567358478776071744);
void car_h_24(double *state, double *unused, double *out_190155790201239850);
void car_H_24(double *state, double *unused, double *out_5549681630173839642);
void car_h_30(double *state, double *unused, double *out_8511282010659690294);
void car_H_30(double *state, double *unused, double *out_5349331862715545011);
void car_h_26(double *state, double *unused, double *out_5186767263433807506);
void car_H_26(double *state, double *unused, double *out_1737167490984728857);
void car_h_27(double *state, double *unused, double *out_3402150099174707791);
void car_H_27(double *state, double *unused, double *out_3174568550915120100);
void car_h_29(double *state, double *unused, double *out_2753297096665952338);
void car_H_29(double *state, double *unused, double *out_5859563207029937195);
void car_h_28(double *state, double *unused, double *out_6022818472511333083);
void car_H_28(double *state, double *unused, double *out_3621193193023961507);
void car_h_31(double *state, double *unused, double *out_8679017429120059325);
void car_H_31(double *state, double *unused, double *out_5509316771735745509);
void car_predict(double *in_x, double *in_P, double *in_Q, double dt);
void car_set_mass(double x);
void car_set_rotational_inertia(double x);
void car_set_center_to_front(double x);
void car_set_center_to_rear(double x);
void car_set_stiffness_front(double x);
void car_set_stiffness_rear(double x);
}