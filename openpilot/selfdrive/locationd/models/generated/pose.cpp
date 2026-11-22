#include "pose.h"

namespace {
#define DIM 18
#define EDIM 18
#define MEDIM 18
typedef void (*Hfun)(double *, double *, double *);
const static double MAHA_THRESH_4 = 7.814727903251177;
const static double MAHA_THRESH_10 = 7.814727903251177;
const static double MAHA_THRESH_13 = 7.814727903251177;
const static double MAHA_THRESH_14 = 7.814727903251177;

/******************************************************************************
 *                      Code generated with SymPy 1.14.0                      *
 *                                                                            *
 *              See http://www.sympy.org/ for more information.               *
 *                                                                            *
 *                         This file is part of 'ekf'                         *
 ******************************************************************************/
void err_fun(double *nom_x, double *delta_x, double *out_124484568216878126) {
   out_124484568216878126[0] = delta_x[0] + nom_x[0];
   out_124484568216878126[1] = delta_x[1] + nom_x[1];
   out_124484568216878126[2] = delta_x[2] + nom_x[2];
   out_124484568216878126[3] = delta_x[3] + nom_x[3];
   out_124484568216878126[4] = delta_x[4] + nom_x[4];
   out_124484568216878126[5] = delta_x[5] + nom_x[5];
   out_124484568216878126[6] = delta_x[6] + nom_x[6];
   out_124484568216878126[7] = delta_x[7] + nom_x[7];
   out_124484568216878126[8] = delta_x[8] + nom_x[8];
   out_124484568216878126[9] = delta_x[9] + nom_x[9];
   out_124484568216878126[10] = delta_x[10] + nom_x[10];
   out_124484568216878126[11] = delta_x[11] + nom_x[11];
   out_124484568216878126[12] = delta_x[12] + nom_x[12];
   out_124484568216878126[13] = delta_x[13] + nom_x[13];
   out_124484568216878126[14] = delta_x[14] + nom_x[14];
   out_124484568216878126[15] = delta_x[15] + nom_x[15];
   out_124484568216878126[16] = delta_x[16] + nom_x[16];
   out_124484568216878126[17] = delta_x[17] + nom_x[17];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_33395899511450204) {
   out_33395899511450204[0] = -nom_x[0] + true_x[0];
   out_33395899511450204[1] = -nom_x[1] + true_x[1];
   out_33395899511450204[2] = -nom_x[2] + true_x[2];
   out_33395899511450204[3] = -nom_x[3] + true_x[3];
   out_33395899511450204[4] = -nom_x[4] + true_x[4];
   out_33395899511450204[5] = -nom_x[5] + true_x[5];
   out_33395899511450204[6] = -nom_x[6] + true_x[6];
   out_33395899511450204[7] = -nom_x[7] + true_x[7];
   out_33395899511450204[8] = -nom_x[8] + true_x[8];
   out_33395899511450204[9] = -nom_x[9] + true_x[9];
   out_33395899511450204[10] = -nom_x[10] + true_x[10];
   out_33395899511450204[11] = -nom_x[11] + true_x[11];
   out_33395899511450204[12] = -nom_x[12] + true_x[12];
   out_33395899511450204[13] = -nom_x[13] + true_x[13];
   out_33395899511450204[14] = -nom_x[14] + true_x[14];
   out_33395899511450204[15] = -nom_x[15] + true_x[15];
   out_33395899511450204[16] = -nom_x[16] + true_x[16];
   out_33395899511450204[17] = -nom_x[17] + true_x[17];
}
void H_mod_fun(double *state, double *out_8589495166796561709) {
   out_8589495166796561709[0] = 1.0;
   out_8589495166796561709[1] = 0.0;
   out_8589495166796561709[2] = 0.0;
   out_8589495166796561709[3] = 0.0;
   out_8589495166796561709[4] = 0.0;
   out_8589495166796561709[5] = 0.0;
   out_8589495166796561709[6] = 0.0;
   out_8589495166796561709[7] = 0.0;
   out_8589495166796561709[8] = 0.0;
   out_8589495166796561709[9] = 0.0;
   out_8589495166796561709[10] = 0.0;
   out_8589495166796561709[11] = 0.0;
   out_8589495166796561709[12] = 0.0;
   out_8589495166796561709[13] = 0.0;
   out_8589495166796561709[14] = 0.0;
   out_8589495166796561709[15] = 0.0;
   out_8589495166796561709[16] = 0.0;
   out_8589495166796561709[17] = 0.0;
   out_8589495166796561709[18] = 0.0;
   out_8589495166796561709[19] = 1.0;
   out_8589495166796561709[20] = 0.0;
   out_8589495166796561709[21] = 0.0;
   out_8589495166796561709[22] = 0.0;
   out_8589495166796561709[23] = 0.0;
   out_8589495166796561709[24] = 0.0;
   out_8589495166796561709[25] = 0.0;
   out_8589495166796561709[26] = 0.0;
   out_8589495166796561709[27] = 0.0;
   out_8589495166796561709[28] = 0.0;
   out_8589495166796561709[29] = 0.0;
   out_8589495166796561709[30] = 0.0;
   out_8589495166796561709[31] = 0.0;
   out_8589495166796561709[32] = 0.0;
   out_8589495166796561709[33] = 0.0;
   out_8589495166796561709[34] = 0.0;
   out_8589495166796561709[35] = 0.0;
   out_8589495166796561709[36] = 0.0;
   out_8589495166796561709[37] = 0.0;
   out_8589495166796561709[38] = 1.0;
   out_8589495166796561709[39] = 0.0;
   out_8589495166796561709[40] = 0.0;
   out_8589495166796561709[41] = 0.0;
   out_8589495166796561709[42] = 0.0;
   out_8589495166796561709[43] = 0.0;
   out_8589495166796561709[44] = 0.0;
   out_8589495166796561709[45] = 0.0;
   out_8589495166796561709[46] = 0.0;
   out_8589495166796561709[47] = 0.0;
   out_8589495166796561709[48] = 0.0;
   out_8589495166796561709[49] = 0.0;
   out_8589495166796561709[50] = 0.0;
   out_8589495166796561709[51] = 0.0;
   out_8589495166796561709[52] = 0.0;
   out_8589495166796561709[53] = 0.0;
   out_8589495166796561709[54] = 0.0;
   out_8589495166796561709[55] = 0.0;
   out_8589495166796561709[56] = 0.0;
   out_8589495166796561709[57] = 1.0;
   out_8589495166796561709[58] = 0.0;
   out_8589495166796561709[59] = 0.0;
   out_8589495166796561709[60] = 0.0;
   out_8589495166796561709[61] = 0.0;
   out_8589495166796561709[62] = 0.0;
   out_8589495166796561709[63] = 0.0;
   out_8589495166796561709[64] = 0.0;
   out_8589495166796561709[65] = 0.0;
   out_8589495166796561709[66] = 0.0;
   out_8589495166796561709[67] = 0.0;
   out_8589495166796561709[68] = 0.0;
   out_8589495166796561709[69] = 0.0;
   out_8589495166796561709[70] = 0.0;
   out_8589495166796561709[71] = 0.0;
   out_8589495166796561709[72] = 0.0;
   out_8589495166796561709[73] = 0.0;
   out_8589495166796561709[74] = 0.0;
   out_8589495166796561709[75] = 0.0;
   out_8589495166796561709[76] = 1.0;
   out_8589495166796561709[77] = 0.0;
   out_8589495166796561709[78] = 0.0;
   out_8589495166796561709[79] = 0.0;
   out_8589495166796561709[80] = 0.0;
   out_8589495166796561709[81] = 0.0;
   out_8589495166796561709[82] = 0.0;
   out_8589495166796561709[83] = 0.0;
   out_8589495166796561709[84] = 0.0;
   out_8589495166796561709[85] = 0.0;
   out_8589495166796561709[86] = 0.0;
   out_8589495166796561709[87] = 0.0;
   out_8589495166796561709[88] = 0.0;
   out_8589495166796561709[89] = 0.0;
   out_8589495166796561709[90] = 0.0;
   out_8589495166796561709[91] = 0.0;
   out_8589495166796561709[92] = 0.0;
   out_8589495166796561709[93] = 0.0;
   out_8589495166796561709[94] = 0.0;
   out_8589495166796561709[95] = 1.0;
   out_8589495166796561709[96] = 0.0;
   out_8589495166796561709[97] = 0.0;
   out_8589495166796561709[98] = 0.0;
   out_8589495166796561709[99] = 0.0;
   out_8589495166796561709[100] = 0.0;
   out_8589495166796561709[101] = 0.0;
   out_8589495166796561709[102] = 0.0;
   out_8589495166796561709[103] = 0.0;
   out_8589495166796561709[104] = 0.0;
   out_8589495166796561709[105] = 0.0;
   out_8589495166796561709[106] = 0.0;
   out_8589495166796561709[107] = 0.0;
   out_8589495166796561709[108] = 0.0;
   out_8589495166796561709[109] = 0.0;
   out_8589495166796561709[110] = 0.0;
   out_8589495166796561709[111] = 0.0;
   out_8589495166796561709[112] = 0.0;
   out_8589495166796561709[113] = 0.0;
   out_8589495166796561709[114] = 1.0;
   out_8589495166796561709[115] = 0.0;
   out_8589495166796561709[116] = 0.0;
   out_8589495166796561709[117] = 0.0;
   out_8589495166796561709[118] = 0.0;
   out_8589495166796561709[119] = 0.0;
   out_8589495166796561709[120] = 0.0;
   out_8589495166796561709[121] = 0.0;
   out_8589495166796561709[122] = 0.0;
   out_8589495166796561709[123] = 0.0;
   out_8589495166796561709[124] = 0.0;
   out_8589495166796561709[125] = 0.0;
   out_8589495166796561709[126] = 0.0;
   out_8589495166796561709[127] = 0.0;
   out_8589495166796561709[128] = 0.0;
   out_8589495166796561709[129] = 0.0;
   out_8589495166796561709[130] = 0.0;
   out_8589495166796561709[131] = 0.0;
   out_8589495166796561709[132] = 0.0;
   out_8589495166796561709[133] = 1.0;
   out_8589495166796561709[134] = 0.0;
   out_8589495166796561709[135] = 0.0;
   out_8589495166796561709[136] = 0.0;
   out_8589495166796561709[137] = 0.0;
   out_8589495166796561709[138] = 0.0;
   out_8589495166796561709[139] = 0.0;
   out_8589495166796561709[140] = 0.0;
   out_8589495166796561709[141] = 0.0;
   out_8589495166796561709[142] = 0.0;
   out_8589495166796561709[143] = 0.0;
   out_8589495166796561709[144] = 0.0;
   out_8589495166796561709[145] = 0.0;
   out_8589495166796561709[146] = 0.0;
   out_8589495166796561709[147] = 0.0;
   out_8589495166796561709[148] = 0.0;
   out_8589495166796561709[149] = 0.0;
   out_8589495166796561709[150] = 0.0;
   out_8589495166796561709[151] = 0.0;
   out_8589495166796561709[152] = 1.0;
   out_8589495166796561709[153] = 0.0;
   out_8589495166796561709[154] = 0.0;
   out_8589495166796561709[155] = 0.0;
   out_8589495166796561709[156] = 0.0;
   out_8589495166796561709[157] = 0.0;
   out_8589495166796561709[158] = 0.0;
   out_8589495166796561709[159] = 0.0;
   out_8589495166796561709[160] = 0.0;
   out_8589495166796561709[161] = 0.0;
   out_8589495166796561709[162] = 0.0;
   out_8589495166796561709[163] = 0.0;
   out_8589495166796561709[164] = 0.0;
   out_8589495166796561709[165] = 0.0;
   out_8589495166796561709[166] = 0.0;
   out_8589495166796561709[167] = 0.0;
   out_8589495166796561709[168] = 0.0;
   out_8589495166796561709[169] = 0.0;
   out_8589495166796561709[170] = 0.0;
   out_8589495166796561709[171] = 1.0;
   out_8589495166796561709[172] = 0.0;
   out_8589495166796561709[173] = 0.0;
   out_8589495166796561709[174] = 0.0;
   out_8589495166796561709[175] = 0.0;
   out_8589495166796561709[176] = 0.0;
   out_8589495166796561709[177] = 0.0;
   out_8589495166796561709[178] = 0.0;
   out_8589495166796561709[179] = 0.0;
   out_8589495166796561709[180] = 0.0;
   out_8589495166796561709[181] = 0.0;
   out_8589495166796561709[182] = 0.0;
   out_8589495166796561709[183] = 0.0;
   out_8589495166796561709[184] = 0.0;
   out_8589495166796561709[185] = 0.0;
   out_8589495166796561709[186] = 0.0;
   out_8589495166796561709[187] = 0.0;
   out_8589495166796561709[188] = 0.0;
   out_8589495166796561709[189] = 0.0;
   out_8589495166796561709[190] = 1.0;
   out_8589495166796561709[191] = 0.0;
   out_8589495166796561709[192] = 0.0;
   out_8589495166796561709[193] = 0.0;
   out_8589495166796561709[194] = 0.0;
   out_8589495166796561709[195] = 0.0;
   out_8589495166796561709[196] = 0.0;
   out_8589495166796561709[197] = 0.0;
   out_8589495166796561709[198] = 0.0;
   out_8589495166796561709[199] = 0.0;
   out_8589495166796561709[200] = 0.0;
   out_8589495166796561709[201] = 0.0;
   out_8589495166796561709[202] = 0.0;
   out_8589495166796561709[203] = 0.0;
   out_8589495166796561709[204] = 0.0;
   out_8589495166796561709[205] = 0.0;
   out_8589495166796561709[206] = 0.0;
   out_8589495166796561709[207] = 0.0;
   out_8589495166796561709[208] = 0.0;
   out_8589495166796561709[209] = 1.0;
   out_8589495166796561709[210] = 0.0;
   out_8589495166796561709[211] = 0.0;
   out_8589495166796561709[212] = 0.0;
   out_8589495166796561709[213] = 0.0;
   out_8589495166796561709[214] = 0.0;
   out_8589495166796561709[215] = 0.0;
   out_8589495166796561709[216] = 0.0;
   out_8589495166796561709[217] = 0.0;
   out_8589495166796561709[218] = 0.0;
   out_8589495166796561709[219] = 0.0;
   out_8589495166796561709[220] = 0.0;
   out_8589495166796561709[221] = 0.0;
   out_8589495166796561709[222] = 0.0;
   out_8589495166796561709[223] = 0.0;
   out_8589495166796561709[224] = 0.0;
   out_8589495166796561709[225] = 0.0;
   out_8589495166796561709[226] = 0.0;
   out_8589495166796561709[227] = 0.0;
   out_8589495166796561709[228] = 1.0;
   out_8589495166796561709[229] = 0.0;
   out_8589495166796561709[230] = 0.0;
   out_8589495166796561709[231] = 0.0;
   out_8589495166796561709[232] = 0.0;
   out_8589495166796561709[233] = 0.0;
   out_8589495166796561709[234] = 0.0;
   out_8589495166796561709[235] = 0.0;
   out_8589495166796561709[236] = 0.0;
   out_8589495166796561709[237] = 0.0;
   out_8589495166796561709[238] = 0.0;
   out_8589495166796561709[239] = 0.0;
   out_8589495166796561709[240] = 0.0;
   out_8589495166796561709[241] = 0.0;
   out_8589495166796561709[242] = 0.0;
   out_8589495166796561709[243] = 0.0;
   out_8589495166796561709[244] = 0.0;
   out_8589495166796561709[245] = 0.0;
   out_8589495166796561709[246] = 0.0;
   out_8589495166796561709[247] = 1.0;
   out_8589495166796561709[248] = 0.0;
   out_8589495166796561709[249] = 0.0;
   out_8589495166796561709[250] = 0.0;
   out_8589495166796561709[251] = 0.0;
   out_8589495166796561709[252] = 0.0;
   out_8589495166796561709[253] = 0.0;
   out_8589495166796561709[254] = 0.0;
   out_8589495166796561709[255] = 0.0;
   out_8589495166796561709[256] = 0.0;
   out_8589495166796561709[257] = 0.0;
   out_8589495166796561709[258] = 0.0;
   out_8589495166796561709[259] = 0.0;
   out_8589495166796561709[260] = 0.0;
   out_8589495166796561709[261] = 0.0;
   out_8589495166796561709[262] = 0.0;
   out_8589495166796561709[263] = 0.0;
   out_8589495166796561709[264] = 0.0;
   out_8589495166796561709[265] = 0.0;
   out_8589495166796561709[266] = 1.0;
   out_8589495166796561709[267] = 0.0;
   out_8589495166796561709[268] = 0.0;
   out_8589495166796561709[269] = 0.0;
   out_8589495166796561709[270] = 0.0;
   out_8589495166796561709[271] = 0.0;
   out_8589495166796561709[272] = 0.0;
   out_8589495166796561709[273] = 0.0;
   out_8589495166796561709[274] = 0.0;
   out_8589495166796561709[275] = 0.0;
   out_8589495166796561709[276] = 0.0;
   out_8589495166796561709[277] = 0.0;
   out_8589495166796561709[278] = 0.0;
   out_8589495166796561709[279] = 0.0;
   out_8589495166796561709[280] = 0.0;
   out_8589495166796561709[281] = 0.0;
   out_8589495166796561709[282] = 0.0;
   out_8589495166796561709[283] = 0.0;
   out_8589495166796561709[284] = 0.0;
   out_8589495166796561709[285] = 1.0;
   out_8589495166796561709[286] = 0.0;
   out_8589495166796561709[287] = 0.0;
   out_8589495166796561709[288] = 0.0;
   out_8589495166796561709[289] = 0.0;
   out_8589495166796561709[290] = 0.0;
   out_8589495166796561709[291] = 0.0;
   out_8589495166796561709[292] = 0.0;
   out_8589495166796561709[293] = 0.0;
   out_8589495166796561709[294] = 0.0;
   out_8589495166796561709[295] = 0.0;
   out_8589495166796561709[296] = 0.0;
   out_8589495166796561709[297] = 0.0;
   out_8589495166796561709[298] = 0.0;
   out_8589495166796561709[299] = 0.0;
   out_8589495166796561709[300] = 0.0;
   out_8589495166796561709[301] = 0.0;
   out_8589495166796561709[302] = 0.0;
   out_8589495166796561709[303] = 0.0;
   out_8589495166796561709[304] = 1.0;
   out_8589495166796561709[305] = 0.0;
   out_8589495166796561709[306] = 0.0;
   out_8589495166796561709[307] = 0.0;
   out_8589495166796561709[308] = 0.0;
   out_8589495166796561709[309] = 0.0;
   out_8589495166796561709[310] = 0.0;
   out_8589495166796561709[311] = 0.0;
   out_8589495166796561709[312] = 0.0;
   out_8589495166796561709[313] = 0.0;
   out_8589495166796561709[314] = 0.0;
   out_8589495166796561709[315] = 0.0;
   out_8589495166796561709[316] = 0.0;
   out_8589495166796561709[317] = 0.0;
   out_8589495166796561709[318] = 0.0;
   out_8589495166796561709[319] = 0.0;
   out_8589495166796561709[320] = 0.0;
   out_8589495166796561709[321] = 0.0;
   out_8589495166796561709[322] = 0.0;
   out_8589495166796561709[323] = 1.0;
}
void f_fun(double *state, double dt, double *out_3036332123814651041) {
   out_3036332123814651041[0] = atan2((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), -(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]));
   out_3036332123814651041[1] = asin(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]));
   out_3036332123814651041[2] = atan2(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), -(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]));
   out_3036332123814651041[3] = dt*state[12] + state[3];
   out_3036332123814651041[4] = dt*state[13] + state[4];
   out_3036332123814651041[5] = dt*state[14] + state[5];
   out_3036332123814651041[6] = state[6];
   out_3036332123814651041[7] = state[7];
   out_3036332123814651041[8] = state[8];
   out_3036332123814651041[9] = state[9];
   out_3036332123814651041[10] = state[10];
   out_3036332123814651041[11] = state[11];
   out_3036332123814651041[12] = state[12];
   out_3036332123814651041[13] = state[13];
   out_3036332123814651041[14] = state[14];
   out_3036332123814651041[15] = state[15];
   out_3036332123814651041[16] = state[16];
   out_3036332123814651041[17] = state[17];
}
void F_fun(double *state, double dt, double *out_3224074787490728613) {
   out_3224074787490728613[0] = ((-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*cos(state[0])*cos(state[1]) - sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*cos(state[0])*cos(state[1]) - sin(dt*state[6])*sin(state[0])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_3224074787490728613[1] = ((-sin(dt*state[6])*sin(dt*state[8]) - sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*cos(state[1]) - (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*sin(state[1]) - sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(state[0]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*sin(state[1]) + (-sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) + sin(dt*state[8])*cos(dt*state[6]))*cos(state[1]) - sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(state[0]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_3224074787490728613[2] = 0;
   out_3224074787490728613[3] = 0;
   out_3224074787490728613[4] = 0;
   out_3224074787490728613[5] = 0;
   out_3224074787490728613[6] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(dt*cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) - dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_3224074787490728613[7] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*sin(dt*state[7])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[6])*sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) - dt*sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[7])*cos(dt*state[6])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[8])*sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]) - dt*sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_3224074787490728613[8] = ((dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((dt*sin(dt*state[6])*sin(dt*state[8]) + dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_3224074787490728613[9] = 0;
   out_3224074787490728613[10] = 0;
   out_3224074787490728613[11] = 0;
   out_3224074787490728613[12] = 0;
   out_3224074787490728613[13] = 0;
   out_3224074787490728613[14] = 0;
   out_3224074787490728613[15] = 0;
   out_3224074787490728613[16] = 0;
   out_3224074787490728613[17] = 0;
   out_3224074787490728613[18] = (-sin(dt*state[7])*sin(state[0])*cos(state[1]) - sin(dt*state[8])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_3224074787490728613[19] = (-sin(dt*state[7])*sin(state[1])*cos(state[0]) + sin(dt*state[8])*sin(state[0])*sin(state[1])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_3224074787490728613[20] = 0;
   out_3224074787490728613[21] = 0;
   out_3224074787490728613[22] = 0;
   out_3224074787490728613[23] = 0;
   out_3224074787490728613[24] = 0;
   out_3224074787490728613[25] = (dt*sin(dt*state[7])*sin(dt*state[8])*sin(state[0])*cos(state[1]) - dt*sin(dt*state[7])*sin(state[1])*cos(dt*state[8]) + dt*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_3224074787490728613[26] = (-dt*sin(dt*state[8])*sin(state[1])*cos(dt*state[7]) - dt*sin(state[0])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_3224074787490728613[27] = 0;
   out_3224074787490728613[28] = 0;
   out_3224074787490728613[29] = 0;
   out_3224074787490728613[30] = 0;
   out_3224074787490728613[31] = 0;
   out_3224074787490728613[32] = 0;
   out_3224074787490728613[33] = 0;
   out_3224074787490728613[34] = 0;
   out_3224074787490728613[35] = 0;
   out_3224074787490728613[36] = ((sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_3224074787490728613[37] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-sin(dt*state[7])*sin(state[2])*cos(state[0])*cos(state[1]) + sin(dt*state[8])*sin(state[0])*sin(state[2])*cos(dt*state[7])*cos(state[1]) - sin(state[1])*sin(state[2])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(-sin(dt*state[7])*cos(state[0])*cos(state[1])*cos(state[2]) + sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1])*cos(state[2]) - sin(state[1])*cos(dt*state[7])*cos(dt*state[8])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_3224074787490728613[38] = ((-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (-sin(state[0])*sin(state[1])*sin(state[2]) - cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_3224074787490728613[39] = 0;
   out_3224074787490728613[40] = 0;
   out_3224074787490728613[41] = 0;
   out_3224074787490728613[42] = 0;
   out_3224074787490728613[43] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(dt*(sin(state[0])*cos(state[2]) - sin(state[1])*sin(state[2])*cos(state[0]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*sin(state[2])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(dt*(-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_3224074787490728613[44] = (dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*sin(state[2])*cos(dt*state[7])*cos(state[1]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + (dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[7])*cos(state[1])*cos(state[2]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_3224074787490728613[45] = 0;
   out_3224074787490728613[46] = 0;
   out_3224074787490728613[47] = 0;
   out_3224074787490728613[48] = 0;
   out_3224074787490728613[49] = 0;
   out_3224074787490728613[50] = 0;
   out_3224074787490728613[51] = 0;
   out_3224074787490728613[52] = 0;
   out_3224074787490728613[53] = 0;
   out_3224074787490728613[54] = 0;
   out_3224074787490728613[55] = 0;
   out_3224074787490728613[56] = 0;
   out_3224074787490728613[57] = 1;
   out_3224074787490728613[58] = 0;
   out_3224074787490728613[59] = 0;
   out_3224074787490728613[60] = 0;
   out_3224074787490728613[61] = 0;
   out_3224074787490728613[62] = 0;
   out_3224074787490728613[63] = 0;
   out_3224074787490728613[64] = 0;
   out_3224074787490728613[65] = 0;
   out_3224074787490728613[66] = dt;
   out_3224074787490728613[67] = 0;
   out_3224074787490728613[68] = 0;
   out_3224074787490728613[69] = 0;
   out_3224074787490728613[70] = 0;
   out_3224074787490728613[71] = 0;
   out_3224074787490728613[72] = 0;
   out_3224074787490728613[73] = 0;
   out_3224074787490728613[74] = 0;
   out_3224074787490728613[75] = 0;
   out_3224074787490728613[76] = 1;
   out_3224074787490728613[77] = 0;
   out_3224074787490728613[78] = 0;
   out_3224074787490728613[79] = 0;
   out_3224074787490728613[80] = 0;
   out_3224074787490728613[81] = 0;
   out_3224074787490728613[82] = 0;
   out_3224074787490728613[83] = 0;
   out_3224074787490728613[84] = 0;
   out_3224074787490728613[85] = dt;
   out_3224074787490728613[86] = 0;
   out_3224074787490728613[87] = 0;
   out_3224074787490728613[88] = 0;
   out_3224074787490728613[89] = 0;
   out_3224074787490728613[90] = 0;
   out_3224074787490728613[91] = 0;
   out_3224074787490728613[92] = 0;
   out_3224074787490728613[93] = 0;
   out_3224074787490728613[94] = 0;
   out_3224074787490728613[95] = 1;
   out_3224074787490728613[96] = 0;
   out_3224074787490728613[97] = 0;
   out_3224074787490728613[98] = 0;
   out_3224074787490728613[99] = 0;
   out_3224074787490728613[100] = 0;
   out_3224074787490728613[101] = 0;
   out_3224074787490728613[102] = 0;
   out_3224074787490728613[103] = 0;
   out_3224074787490728613[104] = dt;
   out_3224074787490728613[105] = 0;
   out_3224074787490728613[106] = 0;
   out_3224074787490728613[107] = 0;
   out_3224074787490728613[108] = 0;
   out_3224074787490728613[109] = 0;
   out_3224074787490728613[110] = 0;
   out_3224074787490728613[111] = 0;
   out_3224074787490728613[112] = 0;
   out_3224074787490728613[113] = 0;
   out_3224074787490728613[114] = 1;
   out_3224074787490728613[115] = 0;
   out_3224074787490728613[116] = 0;
   out_3224074787490728613[117] = 0;
   out_3224074787490728613[118] = 0;
   out_3224074787490728613[119] = 0;
   out_3224074787490728613[120] = 0;
   out_3224074787490728613[121] = 0;
   out_3224074787490728613[122] = 0;
   out_3224074787490728613[123] = 0;
   out_3224074787490728613[124] = 0;
   out_3224074787490728613[125] = 0;
   out_3224074787490728613[126] = 0;
   out_3224074787490728613[127] = 0;
   out_3224074787490728613[128] = 0;
   out_3224074787490728613[129] = 0;
   out_3224074787490728613[130] = 0;
   out_3224074787490728613[131] = 0;
   out_3224074787490728613[132] = 0;
   out_3224074787490728613[133] = 1;
   out_3224074787490728613[134] = 0;
   out_3224074787490728613[135] = 0;
   out_3224074787490728613[136] = 0;
   out_3224074787490728613[137] = 0;
   out_3224074787490728613[138] = 0;
   out_3224074787490728613[139] = 0;
   out_3224074787490728613[140] = 0;
   out_3224074787490728613[141] = 0;
   out_3224074787490728613[142] = 0;
   out_3224074787490728613[143] = 0;
   out_3224074787490728613[144] = 0;
   out_3224074787490728613[145] = 0;
   out_3224074787490728613[146] = 0;
   out_3224074787490728613[147] = 0;
   out_3224074787490728613[148] = 0;
   out_3224074787490728613[149] = 0;
   out_3224074787490728613[150] = 0;
   out_3224074787490728613[151] = 0;
   out_3224074787490728613[152] = 1;
   out_3224074787490728613[153] = 0;
   out_3224074787490728613[154] = 0;
   out_3224074787490728613[155] = 0;
   out_3224074787490728613[156] = 0;
   out_3224074787490728613[157] = 0;
   out_3224074787490728613[158] = 0;
   out_3224074787490728613[159] = 0;
   out_3224074787490728613[160] = 0;
   out_3224074787490728613[161] = 0;
   out_3224074787490728613[162] = 0;
   out_3224074787490728613[163] = 0;
   out_3224074787490728613[164] = 0;
   out_3224074787490728613[165] = 0;
   out_3224074787490728613[166] = 0;
   out_3224074787490728613[167] = 0;
   out_3224074787490728613[168] = 0;
   out_3224074787490728613[169] = 0;
   out_3224074787490728613[170] = 0;
   out_3224074787490728613[171] = 1;
   out_3224074787490728613[172] = 0;
   out_3224074787490728613[173] = 0;
   out_3224074787490728613[174] = 0;
   out_3224074787490728613[175] = 0;
   out_3224074787490728613[176] = 0;
   out_3224074787490728613[177] = 0;
   out_3224074787490728613[178] = 0;
   out_3224074787490728613[179] = 0;
   out_3224074787490728613[180] = 0;
   out_3224074787490728613[181] = 0;
   out_3224074787490728613[182] = 0;
   out_3224074787490728613[183] = 0;
   out_3224074787490728613[184] = 0;
   out_3224074787490728613[185] = 0;
   out_3224074787490728613[186] = 0;
   out_3224074787490728613[187] = 0;
   out_3224074787490728613[188] = 0;
   out_3224074787490728613[189] = 0;
   out_3224074787490728613[190] = 1;
   out_3224074787490728613[191] = 0;
   out_3224074787490728613[192] = 0;
   out_3224074787490728613[193] = 0;
   out_3224074787490728613[194] = 0;
   out_3224074787490728613[195] = 0;
   out_3224074787490728613[196] = 0;
   out_3224074787490728613[197] = 0;
   out_3224074787490728613[198] = 0;
   out_3224074787490728613[199] = 0;
   out_3224074787490728613[200] = 0;
   out_3224074787490728613[201] = 0;
   out_3224074787490728613[202] = 0;
   out_3224074787490728613[203] = 0;
   out_3224074787490728613[204] = 0;
   out_3224074787490728613[205] = 0;
   out_3224074787490728613[206] = 0;
   out_3224074787490728613[207] = 0;
   out_3224074787490728613[208] = 0;
   out_3224074787490728613[209] = 1;
   out_3224074787490728613[210] = 0;
   out_3224074787490728613[211] = 0;
   out_3224074787490728613[212] = 0;
   out_3224074787490728613[213] = 0;
   out_3224074787490728613[214] = 0;
   out_3224074787490728613[215] = 0;
   out_3224074787490728613[216] = 0;
   out_3224074787490728613[217] = 0;
   out_3224074787490728613[218] = 0;
   out_3224074787490728613[219] = 0;
   out_3224074787490728613[220] = 0;
   out_3224074787490728613[221] = 0;
   out_3224074787490728613[222] = 0;
   out_3224074787490728613[223] = 0;
   out_3224074787490728613[224] = 0;
   out_3224074787490728613[225] = 0;
   out_3224074787490728613[226] = 0;
   out_3224074787490728613[227] = 0;
   out_3224074787490728613[228] = 1;
   out_3224074787490728613[229] = 0;
   out_3224074787490728613[230] = 0;
   out_3224074787490728613[231] = 0;
   out_3224074787490728613[232] = 0;
   out_3224074787490728613[233] = 0;
   out_3224074787490728613[234] = 0;
   out_3224074787490728613[235] = 0;
   out_3224074787490728613[236] = 0;
   out_3224074787490728613[237] = 0;
   out_3224074787490728613[238] = 0;
   out_3224074787490728613[239] = 0;
   out_3224074787490728613[240] = 0;
   out_3224074787490728613[241] = 0;
   out_3224074787490728613[242] = 0;
   out_3224074787490728613[243] = 0;
   out_3224074787490728613[244] = 0;
   out_3224074787490728613[245] = 0;
   out_3224074787490728613[246] = 0;
   out_3224074787490728613[247] = 1;
   out_3224074787490728613[248] = 0;
   out_3224074787490728613[249] = 0;
   out_3224074787490728613[250] = 0;
   out_3224074787490728613[251] = 0;
   out_3224074787490728613[252] = 0;
   out_3224074787490728613[253] = 0;
   out_3224074787490728613[254] = 0;
   out_3224074787490728613[255] = 0;
   out_3224074787490728613[256] = 0;
   out_3224074787490728613[257] = 0;
   out_3224074787490728613[258] = 0;
   out_3224074787490728613[259] = 0;
   out_3224074787490728613[260] = 0;
   out_3224074787490728613[261] = 0;
   out_3224074787490728613[262] = 0;
   out_3224074787490728613[263] = 0;
   out_3224074787490728613[264] = 0;
   out_3224074787490728613[265] = 0;
   out_3224074787490728613[266] = 1;
   out_3224074787490728613[267] = 0;
   out_3224074787490728613[268] = 0;
   out_3224074787490728613[269] = 0;
   out_3224074787490728613[270] = 0;
   out_3224074787490728613[271] = 0;
   out_3224074787490728613[272] = 0;
   out_3224074787490728613[273] = 0;
   out_3224074787490728613[274] = 0;
   out_3224074787490728613[275] = 0;
   out_3224074787490728613[276] = 0;
   out_3224074787490728613[277] = 0;
   out_3224074787490728613[278] = 0;
   out_3224074787490728613[279] = 0;
   out_3224074787490728613[280] = 0;
   out_3224074787490728613[281] = 0;
   out_3224074787490728613[282] = 0;
   out_3224074787490728613[283] = 0;
   out_3224074787490728613[284] = 0;
   out_3224074787490728613[285] = 1;
   out_3224074787490728613[286] = 0;
   out_3224074787490728613[287] = 0;
   out_3224074787490728613[288] = 0;
   out_3224074787490728613[289] = 0;
   out_3224074787490728613[290] = 0;
   out_3224074787490728613[291] = 0;
   out_3224074787490728613[292] = 0;
   out_3224074787490728613[293] = 0;
   out_3224074787490728613[294] = 0;
   out_3224074787490728613[295] = 0;
   out_3224074787490728613[296] = 0;
   out_3224074787490728613[297] = 0;
   out_3224074787490728613[298] = 0;
   out_3224074787490728613[299] = 0;
   out_3224074787490728613[300] = 0;
   out_3224074787490728613[301] = 0;
   out_3224074787490728613[302] = 0;
   out_3224074787490728613[303] = 0;
   out_3224074787490728613[304] = 1;
   out_3224074787490728613[305] = 0;
   out_3224074787490728613[306] = 0;
   out_3224074787490728613[307] = 0;
   out_3224074787490728613[308] = 0;
   out_3224074787490728613[309] = 0;
   out_3224074787490728613[310] = 0;
   out_3224074787490728613[311] = 0;
   out_3224074787490728613[312] = 0;
   out_3224074787490728613[313] = 0;
   out_3224074787490728613[314] = 0;
   out_3224074787490728613[315] = 0;
   out_3224074787490728613[316] = 0;
   out_3224074787490728613[317] = 0;
   out_3224074787490728613[318] = 0;
   out_3224074787490728613[319] = 0;
   out_3224074787490728613[320] = 0;
   out_3224074787490728613[321] = 0;
   out_3224074787490728613[322] = 0;
   out_3224074787490728613[323] = 1;
}
void h_4(double *state, double *unused, double *out_1881987918564533025) {
   out_1881987918564533025[0] = state[6] + state[9];
   out_1881987918564533025[1] = state[7] + state[10];
   out_1881987918564533025[2] = state[8] + state[11];
}
void H_4(double *state, double *unused, double *out_2928726227222269101) {
   out_2928726227222269101[0] = 0;
   out_2928726227222269101[1] = 0;
   out_2928726227222269101[2] = 0;
   out_2928726227222269101[3] = 0;
   out_2928726227222269101[4] = 0;
   out_2928726227222269101[5] = 0;
   out_2928726227222269101[6] = 1;
   out_2928726227222269101[7] = 0;
   out_2928726227222269101[8] = 0;
   out_2928726227222269101[9] = 1;
   out_2928726227222269101[10] = 0;
   out_2928726227222269101[11] = 0;
   out_2928726227222269101[12] = 0;
   out_2928726227222269101[13] = 0;
   out_2928726227222269101[14] = 0;
   out_2928726227222269101[15] = 0;
   out_2928726227222269101[16] = 0;
   out_2928726227222269101[17] = 0;
   out_2928726227222269101[18] = 0;
   out_2928726227222269101[19] = 0;
   out_2928726227222269101[20] = 0;
   out_2928726227222269101[21] = 0;
   out_2928726227222269101[22] = 0;
   out_2928726227222269101[23] = 0;
   out_2928726227222269101[24] = 0;
   out_2928726227222269101[25] = 1;
   out_2928726227222269101[26] = 0;
   out_2928726227222269101[27] = 0;
   out_2928726227222269101[28] = 1;
   out_2928726227222269101[29] = 0;
   out_2928726227222269101[30] = 0;
   out_2928726227222269101[31] = 0;
   out_2928726227222269101[32] = 0;
   out_2928726227222269101[33] = 0;
   out_2928726227222269101[34] = 0;
   out_2928726227222269101[35] = 0;
   out_2928726227222269101[36] = 0;
   out_2928726227222269101[37] = 0;
   out_2928726227222269101[38] = 0;
   out_2928726227222269101[39] = 0;
   out_2928726227222269101[40] = 0;
   out_2928726227222269101[41] = 0;
   out_2928726227222269101[42] = 0;
   out_2928726227222269101[43] = 0;
   out_2928726227222269101[44] = 1;
   out_2928726227222269101[45] = 0;
   out_2928726227222269101[46] = 0;
   out_2928726227222269101[47] = 1;
   out_2928726227222269101[48] = 0;
   out_2928726227222269101[49] = 0;
   out_2928726227222269101[50] = 0;
   out_2928726227222269101[51] = 0;
   out_2928726227222269101[52] = 0;
   out_2928726227222269101[53] = 0;
}
void h_10(double *state, double *unused, double *out_6980138353716057693) {
   out_6980138353716057693[0] = 9.8100000000000005*sin(state[1]) - state[4]*state[8] + state[5]*state[7] + state[12] + state[15];
   out_6980138353716057693[1] = -9.8100000000000005*sin(state[0])*cos(state[1]) + state[3]*state[8] - state[5]*state[6] + state[13] + state[16];
   out_6980138353716057693[2] = -9.8100000000000005*cos(state[0])*cos(state[1]) - state[3]*state[7] + state[4]*state[6] + state[14] + state[17];
}
void H_10(double *state, double *unused, double *out_3120928681113084341) {
   out_3120928681113084341[0] = 0;
   out_3120928681113084341[1] = 9.8100000000000005*cos(state[1]);
   out_3120928681113084341[2] = 0;
   out_3120928681113084341[3] = 0;
   out_3120928681113084341[4] = -state[8];
   out_3120928681113084341[5] = state[7];
   out_3120928681113084341[6] = 0;
   out_3120928681113084341[7] = state[5];
   out_3120928681113084341[8] = -state[4];
   out_3120928681113084341[9] = 0;
   out_3120928681113084341[10] = 0;
   out_3120928681113084341[11] = 0;
   out_3120928681113084341[12] = 1;
   out_3120928681113084341[13] = 0;
   out_3120928681113084341[14] = 0;
   out_3120928681113084341[15] = 1;
   out_3120928681113084341[16] = 0;
   out_3120928681113084341[17] = 0;
   out_3120928681113084341[18] = -9.8100000000000005*cos(state[0])*cos(state[1]);
   out_3120928681113084341[19] = 9.8100000000000005*sin(state[0])*sin(state[1]);
   out_3120928681113084341[20] = 0;
   out_3120928681113084341[21] = state[8];
   out_3120928681113084341[22] = 0;
   out_3120928681113084341[23] = -state[6];
   out_3120928681113084341[24] = -state[5];
   out_3120928681113084341[25] = 0;
   out_3120928681113084341[26] = state[3];
   out_3120928681113084341[27] = 0;
   out_3120928681113084341[28] = 0;
   out_3120928681113084341[29] = 0;
   out_3120928681113084341[30] = 0;
   out_3120928681113084341[31] = 1;
   out_3120928681113084341[32] = 0;
   out_3120928681113084341[33] = 0;
   out_3120928681113084341[34] = 1;
   out_3120928681113084341[35] = 0;
   out_3120928681113084341[36] = 9.8100000000000005*sin(state[0])*cos(state[1]);
   out_3120928681113084341[37] = 9.8100000000000005*sin(state[1])*cos(state[0]);
   out_3120928681113084341[38] = 0;
   out_3120928681113084341[39] = -state[7];
   out_3120928681113084341[40] = state[6];
   out_3120928681113084341[41] = 0;
   out_3120928681113084341[42] = state[4];
   out_3120928681113084341[43] = -state[3];
   out_3120928681113084341[44] = 0;
   out_3120928681113084341[45] = 0;
   out_3120928681113084341[46] = 0;
   out_3120928681113084341[47] = 0;
   out_3120928681113084341[48] = 0;
   out_3120928681113084341[49] = 0;
   out_3120928681113084341[50] = 1;
   out_3120928681113084341[51] = 0;
   out_3120928681113084341[52] = 0;
   out_3120928681113084341[53] = 1;
}
void h_13(double *state, double *unused, double *out_7194230607607926667) {
   out_7194230607607926667[0] = state[3];
   out_7194230607607926667[1] = state[4];
   out_7194230607607926667[2] = state[5];
}
void H_13(double *state, double *unused, double *out_6762481690524793125) {
   out_6762481690524793125[0] = 0;
   out_6762481690524793125[1] = 0;
   out_6762481690524793125[2] = 0;
   out_6762481690524793125[3] = 1;
   out_6762481690524793125[4] = 0;
   out_6762481690524793125[5] = 0;
   out_6762481690524793125[6] = 0;
   out_6762481690524793125[7] = 0;
   out_6762481690524793125[8] = 0;
   out_6762481690524793125[9] = 0;
   out_6762481690524793125[10] = 0;
   out_6762481690524793125[11] = 0;
   out_6762481690524793125[12] = 0;
   out_6762481690524793125[13] = 0;
   out_6762481690524793125[14] = 0;
   out_6762481690524793125[15] = 0;
   out_6762481690524793125[16] = 0;
   out_6762481690524793125[17] = 0;
   out_6762481690524793125[18] = 0;
   out_6762481690524793125[19] = 0;
   out_6762481690524793125[20] = 0;
   out_6762481690524793125[21] = 0;
   out_6762481690524793125[22] = 1;
   out_6762481690524793125[23] = 0;
   out_6762481690524793125[24] = 0;
   out_6762481690524793125[25] = 0;
   out_6762481690524793125[26] = 0;
   out_6762481690524793125[27] = 0;
   out_6762481690524793125[28] = 0;
   out_6762481690524793125[29] = 0;
   out_6762481690524793125[30] = 0;
   out_6762481690524793125[31] = 0;
   out_6762481690524793125[32] = 0;
   out_6762481690524793125[33] = 0;
   out_6762481690524793125[34] = 0;
   out_6762481690524793125[35] = 0;
   out_6762481690524793125[36] = 0;
   out_6762481690524793125[37] = 0;
   out_6762481690524793125[38] = 0;
   out_6762481690524793125[39] = 0;
   out_6762481690524793125[40] = 0;
   out_6762481690524793125[41] = 1;
   out_6762481690524793125[42] = 0;
   out_6762481690524793125[43] = 0;
   out_6762481690524793125[44] = 0;
   out_6762481690524793125[45] = 0;
   out_6762481690524793125[46] = 0;
   out_6762481690524793125[47] = 0;
   out_6762481690524793125[48] = 0;
   out_6762481690524793125[49] = 0;
   out_6762481690524793125[50] = 0;
   out_6762481690524793125[51] = 0;
   out_6762481690524793125[52] = 0;
   out_6762481690524793125[53] = 0;
}
void h_14(double *state, double *unused, double *out_1478550648350481724) {
   out_1478550648350481724[0] = state[6];
   out_1478550648350481724[1] = state[7];
   out_1478550648350481724[2] = state[8];
}
void H_14(double *state, double *unused, double *out_8036872031207542091) {
   out_8036872031207542091[0] = 0;
   out_8036872031207542091[1] = 0;
   out_8036872031207542091[2] = 0;
   out_8036872031207542091[3] = 0;
   out_8036872031207542091[4] = 0;
   out_8036872031207542091[5] = 0;
   out_8036872031207542091[6] = 1;
   out_8036872031207542091[7] = 0;
   out_8036872031207542091[8] = 0;
   out_8036872031207542091[9] = 0;
   out_8036872031207542091[10] = 0;
   out_8036872031207542091[11] = 0;
   out_8036872031207542091[12] = 0;
   out_8036872031207542091[13] = 0;
   out_8036872031207542091[14] = 0;
   out_8036872031207542091[15] = 0;
   out_8036872031207542091[16] = 0;
   out_8036872031207542091[17] = 0;
   out_8036872031207542091[18] = 0;
   out_8036872031207542091[19] = 0;
   out_8036872031207542091[20] = 0;
   out_8036872031207542091[21] = 0;
   out_8036872031207542091[22] = 0;
   out_8036872031207542091[23] = 0;
   out_8036872031207542091[24] = 0;
   out_8036872031207542091[25] = 1;
   out_8036872031207542091[26] = 0;
   out_8036872031207542091[27] = 0;
   out_8036872031207542091[28] = 0;
   out_8036872031207542091[29] = 0;
   out_8036872031207542091[30] = 0;
   out_8036872031207542091[31] = 0;
   out_8036872031207542091[32] = 0;
   out_8036872031207542091[33] = 0;
   out_8036872031207542091[34] = 0;
   out_8036872031207542091[35] = 0;
   out_8036872031207542091[36] = 0;
   out_8036872031207542091[37] = 0;
   out_8036872031207542091[38] = 0;
   out_8036872031207542091[39] = 0;
   out_8036872031207542091[40] = 0;
   out_8036872031207542091[41] = 0;
   out_8036872031207542091[42] = 0;
   out_8036872031207542091[43] = 0;
   out_8036872031207542091[44] = 1;
   out_8036872031207542091[45] = 0;
   out_8036872031207542091[46] = 0;
   out_8036872031207542091[47] = 0;
   out_8036872031207542091[48] = 0;
   out_8036872031207542091[49] = 0;
   out_8036872031207542091[50] = 0;
   out_8036872031207542091[51] = 0;
   out_8036872031207542091[52] = 0;
   out_8036872031207542091[53] = 0;
}
#include <eigen3/Eigen/Dense>
#include <iostream>

typedef Eigen::Matrix<double, DIM, DIM, Eigen::RowMajor> DDM;
typedef Eigen::Matrix<double, EDIM, EDIM, Eigen::RowMajor> EEM;
typedef Eigen::Matrix<double, DIM, EDIM, Eigen::RowMajor> DEM;

void predict(double *in_x, double *in_P, double *in_Q, double dt) {
  typedef Eigen::Matrix<double, MEDIM, MEDIM, Eigen::RowMajor> RRM;

  double nx[DIM] = {0};
  double in_F[EDIM*EDIM] = {0};

  // functions from sympy
  f_fun(in_x, dt, nx);
  F_fun(in_x, dt, in_F);


  EEM F(in_F);
  EEM P(in_P);
  EEM Q(in_Q);

  RRM F_main = F.topLeftCorner(MEDIM, MEDIM);
  P.topLeftCorner(MEDIM, MEDIM) = (F_main * P.topLeftCorner(MEDIM, MEDIM)) * F_main.transpose();
  P.topRightCorner(MEDIM, EDIM - MEDIM) = F_main * P.topRightCorner(MEDIM, EDIM - MEDIM);
  P.bottomLeftCorner(EDIM - MEDIM, MEDIM) = P.bottomLeftCorner(EDIM - MEDIM, MEDIM) * F_main.transpose();

  P = P + dt*Q;

  // copy out state
  memcpy(in_x, nx, DIM * sizeof(double));
  memcpy(in_P, P.data(), EDIM * EDIM * sizeof(double));
}

// note: extra_args dim only correct when null space projecting
// otherwise 1
template <int ZDIM, int EADIM, bool MAHA_TEST>
void update(double *in_x, double *in_P, Hfun h_fun, Hfun H_fun, Hfun Hea_fun, double *in_z, double *in_R, double *in_ea, double MAHA_THRESHOLD) {
  typedef Eigen::Matrix<double, ZDIM, ZDIM, Eigen::RowMajor> ZZM;
  typedef Eigen::Matrix<double, ZDIM, DIM, Eigen::RowMajor> ZDM;
  typedef Eigen::Matrix<double, Eigen::Dynamic, EDIM, Eigen::RowMajor> XEM;
  //typedef Eigen::Matrix<double, EDIM, ZDIM, Eigen::RowMajor> EZM;
  typedef Eigen::Matrix<double, Eigen::Dynamic, 1> X1M;
  typedef Eigen::Matrix<double, Eigen::Dynamic, Eigen::Dynamic, Eigen::RowMajor> XXM;

  double in_hx[ZDIM] = {0};
  double in_H[ZDIM * DIM] = {0};
  double in_H_mod[EDIM * DIM] = {0};
  double delta_x[EDIM] = {0};
  double x_new[DIM] = {0};


  // state x, P
  Eigen::Matrix<double, ZDIM, 1> z(in_z);
  EEM P(in_P);
  ZZM pre_R(in_R);

  // functions from sympy
  h_fun(in_x, in_ea, in_hx);
  H_fun(in_x, in_ea, in_H);
  ZDM pre_H(in_H);

  // get y (y = z - hx)
  Eigen::Matrix<double, ZDIM, 1> pre_y(in_hx); pre_y = z - pre_y;
  X1M y; XXM H; XXM R;
  if (Hea_fun){
    typedef Eigen::Matrix<double, ZDIM, EADIM, Eigen::RowMajor> ZAM;
    double in_Hea[ZDIM * EADIM] = {0};
    Hea_fun(in_x, in_ea, in_Hea);
    ZAM Hea(in_Hea);
    XXM A = Hea.transpose().fullPivLu().kernel();


    y = A.transpose() * pre_y;
    H = A.transpose() * pre_H;
    R = A.transpose() * pre_R * A;
  } else {
    y = pre_y;
    H = pre_H;
    R = pre_R;
  }
  // get modified H
  H_mod_fun(in_x, in_H_mod);
  DEM H_mod(in_H_mod);
  XEM H_err = H * H_mod;

  // Do mahalobis distance test
  if (MAHA_TEST){
    XXM a = (H_err * P * H_err.transpose() + R).inverse();
    double maha_dist = y.transpose() * a * y;
    if (maha_dist > MAHA_THRESHOLD){
      R = 1.0e16 * R;
    }
  }

  // Outlier resilient weighting
  double weight = 1;//(1.5)/(1 + y.squaredNorm()/R.sum());

  // kalman gains and I_KH
  XXM S = ((H_err * P) * H_err.transpose()) + R/weight;
  XEM KT = S.fullPivLu().solve(H_err * P.transpose());
  //EZM K = KT.transpose(); TODO: WHY DOES THIS NOT COMPILE?
  //EZM K = S.fullPivLu().solve(H_err * P.transpose()).transpose();
  //std::cout << "Here is the matrix rot:\n" << K << std::endl;
  EEM I_KH = Eigen::Matrix<double, EDIM, EDIM>::Identity() - (KT.transpose() * H_err);

  // update state by injecting dx
  Eigen::Matrix<double, EDIM, 1> dx(delta_x);
  dx  = (KT.transpose() * y);
  memcpy(delta_x, dx.data(), EDIM * sizeof(double));
  err_fun(in_x, delta_x, x_new);
  Eigen::Matrix<double, DIM, 1> x(x_new);

  // update cov
  P = ((I_KH * P) * I_KH.transpose()) + ((KT.transpose() * R) * KT);

  // copy out state
  memcpy(in_x, x.data(), DIM * sizeof(double));
  memcpy(in_P, P.data(), EDIM * EDIM * sizeof(double));
  memcpy(in_z, y.data(), y.rows() * sizeof(double));
}




}
extern "C" {

void pose_update_4(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<3, 3, 0>(in_x, in_P, h_4, H_4, NULL, in_z, in_R, in_ea, MAHA_THRESH_4);
}
void pose_update_10(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<3, 3, 0>(in_x, in_P, h_10, H_10, NULL, in_z, in_R, in_ea, MAHA_THRESH_10);
}
void pose_update_13(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<3, 3, 0>(in_x, in_P, h_13, H_13, NULL, in_z, in_R, in_ea, MAHA_THRESH_13);
}
void pose_update_14(double *in_x, double *in_P, double *in_z, double *in_R, double *in_ea) {
  update<3, 3, 0>(in_x, in_P, h_14, H_14, NULL, in_z, in_R, in_ea, MAHA_THRESH_14);
}
void pose_err_fun(double *nom_x, double *delta_x, double *out_124484568216878126) {
  err_fun(nom_x, delta_x, out_124484568216878126);
}
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_33395899511450204) {
  inv_err_fun(nom_x, true_x, out_33395899511450204);
}
void pose_H_mod_fun(double *state, double *out_8589495166796561709) {
  H_mod_fun(state, out_8589495166796561709);
}
void pose_f_fun(double *state, double dt, double *out_3036332123814651041) {
  f_fun(state,  dt, out_3036332123814651041);
}
void pose_F_fun(double *state, double dt, double *out_3224074787490728613) {
  F_fun(state,  dt, out_3224074787490728613);
}
void pose_h_4(double *state, double *unused, double *out_1881987918564533025) {
  h_4(state, unused, out_1881987918564533025);
}
void pose_H_4(double *state, double *unused, double *out_2928726227222269101) {
  H_4(state, unused, out_2928726227222269101);
}
void pose_h_10(double *state, double *unused, double *out_6980138353716057693) {
  h_10(state, unused, out_6980138353716057693);
}
void pose_H_10(double *state, double *unused, double *out_3120928681113084341) {
  H_10(state, unused, out_3120928681113084341);
}
void pose_h_13(double *state, double *unused, double *out_7194230607607926667) {
  h_13(state, unused, out_7194230607607926667);
}
void pose_H_13(double *state, double *unused, double *out_6762481690524793125) {
  H_13(state, unused, out_6762481690524793125);
}
void pose_h_14(double *state, double *unused, double *out_1478550648350481724) {
  h_14(state, unused, out_1478550648350481724);
}
void pose_H_14(double *state, double *unused, double *out_8036872031207542091) {
  H_14(state, unused, out_8036872031207542091);
}
void pose_predict(double *in_x, double *in_P, double *in_Q, double dt) {
  predict(in_x, in_P, in_Q, dt);
}
}

const EKF pose = {
  .name = "pose",
  .kinds = { 4, 10, 13, 14 },
  .feature_kinds = {  },
  .f_fun = pose_f_fun,
  .F_fun = pose_F_fun,
  .err_fun = pose_err_fun,
  .inv_err_fun = pose_inv_err_fun,
  .H_mod_fun = pose_H_mod_fun,
  .predict = pose_predict,
  .hs = {
    { 4, pose_h_4 },
    { 10, pose_h_10 },
    { 13, pose_h_13 },
    { 14, pose_h_14 },
  },
  .Hs = {
    { 4, pose_H_4 },
    { 10, pose_H_10 },
    { 13, pose_H_13 },
    { 14, pose_H_14 },
  },
  .updates = {
    { 4, pose_update_4 },
    { 10, pose_update_10 },
    { 13, pose_update_13 },
    { 14, pose_update_14 },
  },
  .Hes = {
  },
  .sets = {
  },
  .extra_routines = {
  },
};

ekf_lib_init(pose)
