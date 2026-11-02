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
void err_fun(double *nom_x, double *delta_x, double *out_4327736854274805720) {
   out_4327736854274805720[0] = delta_x[0] + nom_x[0];
   out_4327736854274805720[1] = delta_x[1] + nom_x[1];
   out_4327736854274805720[2] = delta_x[2] + nom_x[2];
   out_4327736854274805720[3] = delta_x[3] + nom_x[3];
   out_4327736854274805720[4] = delta_x[4] + nom_x[4];
   out_4327736854274805720[5] = delta_x[5] + nom_x[5];
   out_4327736854274805720[6] = delta_x[6] + nom_x[6];
   out_4327736854274805720[7] = delta_x[7] + nom_x[7];
   out_4327736854274805720[8] = delta_x[8] + nom_x[8];
   out_4327736854274805720[9] = delta_x[9] + nom_x[9];
   out_4327736854274805720[10] = delta_x[10] + nom_x[10];
   out_4327736854274805720[11] = delta_x[11] + nom_x[11];
   out_4327736854274805720[12] = delta_x[12] + nom_x[12];
   out_4327736854274805720[13] = delta_x[13] + nom_x[13];
   out_4327736854274805720[14] = delta_x[14] + nom_x[14];
   out_4327736854274805720[15] = delta_x[15] + nom_x[15];
   out_4327736854274805720[16] = delta_x[16] + nom_x[16];
   out_4327736854274805720[17] = delta_x[17] + nom_x[17];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_542433210393079092) {
   out_542433210393079092[0] = -nom_x[0] + true_x[0];
   out_542433210393079092[1] = -nom_x[1] + true_x[1];
   out_542433210393079092[2] = -nom_x[2] + true_x[2];
   out_542433210393079092[3] = -nom_x[3] + true_x[3];
   out_542433210393079092[4] = -nom_x[4] + true_x[4];
   out_542433210393079092[5] = -nom_x[5] + true_x[5];
   out_542433210393079092[6] = -nom_x[6] + true_x[6];
   out_542433210393079092[7] = -nom_x[7] + true_x[7];
   out_542433210393079092[8] = -nom_x[8] + true_x[8];
   out_542433210393079092[9] = -nom_x[9] + true_x[9];
   out_542433210393079092[10] = -nom_x[10] + true_x[10];
   out_542433210393079092[11] = -nom_x[11] + true_x[11];
   out_542433210393079092[12] = -nom_x[12] + true_x[12];
   out_542433210393079092[13] = -nom_x[13] + true_x[13];
   out_542433210393079092[14] = -nom_x[14] + true_x[14];
   out_542433210393079092[15] = -nom_x[15] + true_x[15];
   out_542433210393079092[16] = -nom_x[16] + true_x[16];
   out_542433210393079092[17] = -nom_x[17] + true_x[17];
}
void H_mod_fun(double *state, double *out_2780673785235724857) {
   out_2780673785235724857[0] = 1.0;
   out_2780673785235724857[1] = 0.0;
   out_2780673785235724857[2] = 0.0;
   out_2780673785235724857[3] = 0.0;
   out_2780673785235724857[4] = 0.0;
   out_2780673785235724857[5] = 0.0;
   out_2780673785235724857[6] = 0.0;
   out_2780673785235724857[7] = 0.0;
   out_2780673785235724857[8] = 0.0;
   out_2780673785235724857[9] = 0.0;
   out_2780673785235724857[10] = 0.0;
   out_2780673785235724857[11] = 0.0;
   out_2780673785235724857[12] = 0.0;
   out_2780673785235724857[13] = 0.0;
   out_2780673785235724857[14] = 0.0;
   out_2780673785235724857[15] = 0.0;
   out_2780673785235724857[16] = 0.0;
   out_2780673785235724857[17] = 0.0;
   out_2780673785235724857[18] = 0.0;
   out_2780673785235724857[19] = 1.0;
   out_2780673785235724857[20] = 0.0;
   out_2780673785235724857[21] = 0.0;
   out_2780673785235724857[22] = 0.0;
   out_2780673785235724857[23] = 0.0;
   out_2780673785235724857[24] = 0.0;
   out_2780673785235724857[25] = 0.0;
   out_2780673785235724857[26] = 0.0;
   out_2780673785235724857[27] = 0.0;
   out_2780673785235724857[28] = 0.0;
   out_2780673785235724857[29] = 0.0;
   out_2780673785235724857[30] = 0.0;
   out_2780673785235724857[31] = 0.0;
   out_2780673785235724857[32] = 0.0;
   out_2780673785235724857[33] = 0.0;
   out_2780673785235724857[34] = 0.0;
   out_2780673785235724857[35] = 0.0;
   out_2780673785235724857[36] = 0.0;
   out_2780673785235724857[37] = 0.0;
   out_2780673785235724857[38] = 1.0;
   out_2780673785235724857[39] = 0.0;
   out_2780673785235724857[40] = 0.0;
   out_2780673785235724857[41] = 0.0;
   out_2780673785235724857[42] = 0.0;
   out_2780673785235724857[43] = 0.0;
   out_2780673785235724857[44] = 0.0;
   out_2780673785235724857[45] = 0.0;
   out_2780673785235724857[46] = 0.0;
   out_2780673785235724857[47] = 0.0;
   out_2780673785235724857[48] = 0.0;
   out_2780673785235724857[49] = 0.0;
   out_2780673785235724857[50] = 0.0;
   out_2780673785235724857[51] = 0.0;
   out_2780673785235724857[52] = 0.0;
   out_2780673785235724857[53] = 0.0;
   out_2780673785235724857[54] = 0.0;
   out_2780673785235724857[55] = 0.0;
   out_2780673785235724857[56] = 0.0;
   out_2780673785235724857[57] = 1.0;
   out_2780673785235724857[58] = 0.0;
   out_2780673785235724857[59] = 0.0;
   out_2780673785235724857[60] = 0.0;
   out_2780673785235724857[61] = 0.0;
   out_2780673785235724857[62] = 0.0;
   out_2780673785235724857[63] = 0.0;
   out_2780673785235724857[64] = 0.0;
   out_2780673785235724857[65] = 0.0;
   out_2780673785235724857[66] = 0.0;
   out_2780673785235724857[67] = 0.0;
   out_2780673785235724857[68] = 0.0;
   out_2780673785235724857[69] = 0.0;
   out_2780673785235724857[70] = 0.0;
   out_2780673785235724857[71] = 0.0;
   out_2780673785235724857[72] = 0.0;
   out_2780673785235724857[73] = 0.0;
   out_2780673785235724857[74] = 0.0;
   out_2780673785235724857[75] = 0.0;
   out_2780673785235724857[76] = 1.0;
   out_2780673785235724857[77] = 0.0;
   out_2780673785235724857[78] = 0.0;
   out_2780673785235724857[79] = 0.0;
   out_2780673785235724857[80] = 0.0;
   out_2780673785235724857[81] = 0.0;
   out_2780673785235724857[82] = 0.0;
   out_2780673785235724857[83] = 0.0;
   out_2780673785235724857[84] = 0.0;
   out_2780673785235724857[85] = 0.0;
   out_2780673785235724857[86] = 0.0;
   out_2780673785235724857[87] = 0.0;
   out_2780673785235724857[88] = 0.0;
   out_2780673785235724857[89] = 0.0;
   out_2780673785235724857[90] = 0.0;
   out_2780673785235724857[91] = 0.0;
   out_2780673785235724857[92] = 0.0;
   out_2780673785235724857[93] = 0.0;
   out_2780673785235724857[94] = 0.0;
   out_2780673785235724857[95] = 1.0;
   out_2780673785235724857[96] = 0.0;
   out_2780673785235724857[97] = 0.0;
   out_2780673785235724857[98] = 0.0;
   out_2780673785235724857[99] = 0.0;
   out_2780673785235724857[100] = 0.0;
   out_2780673785235724857[101] = 0.0;
   out_2780673785235724857[102] = 0.0;
   out_2780673785235724857[103] = 0.0;
   out_2780673785235724857[104] = 0.0;
   out_2780673785235724857[105] = 0.0;
   out_2780673785235724857[106] = 0.0;
   out_2780673785235724857[107] = 0.0;
   out_2780673785235724857[108] = 0.0;
   out_2780673785235724857[109] = 0.0;
   out_2780673785235724857[110] = 0.0;
   out_2780673785235724857[111] = 0.0;
   out_2780673785235724857[112] = 0.0;
   out_2780673785235724857[113] = 0.0;
   out_2780673785235724857[114] = 1.0;
   out_2780673785235724857[115] = 0.0;
   out_2780673785235724857[116] = 0.0;
   out_2780673785235724857[117] = 0.0;
   out_2780673785235724857[118] = 0.0;
   out_2780673785235724857[119] = 0.0;
   out_2780673785235724857[120] = 0.0;
   out_2780673785235724857[121] = 0.0;
   out_2780673785235724857[122] = 0.0;
   out_2780673785235724857[123] = 0.0;
   out_2780673785235724857[124] = 0.0;
   out_2780673785235724857[125] = 0.0;
   out_2780673785235724857[126] = 0.0;
   out_2780673785235724857[127] = 0.0;
   out_2780673785235724857[128] = 0.0;
   out_2780673785235724857[129] = 0.0;
   out_2780673785235724857[130] = 0.0;
   out_2780673785235724857[131] = 0.0;
   out_2780673785235724857[132] = 0.0;
   out_2780673785235724857[133] = 1.0;
   out_2780673785235724857[134] = 0.0;
   out_2780673785235724857[135] = 0.0;
   out_2780673785235724857[136] = 0.0;
   out_2780673785235724857[137] = 0.0;
   out_2780673785235724857[138] = 0.0;
   out_2780673785235724857[139] = 0.0;
   out_2780673785235724857[140] = 0.0;
   out_2780673785235724857[141] = 0.0;
   out_2780673785235724857[142] = 0.0;
   out_2780673785235724857[143] = 0.0;
   out_2780673785235724857[144] = 0.0;
   out_2780673785235724857[145] = 0.0;
   out_2780673785235724857[146] = 0.0;
   out_2780673785235724857[147] = 0.0;
   out_2780673785235724857[148] = 0.0;
   out_2780673785235724857[149] = 0.0;
   out_2780673785235724857[150] = 0.0;
   out_2780673785235724857[151] = 0.0;
   out_2780673785235724857[152] = 1.0;
   out_2780673785235724857[153] = 0.0;
   out_2780673785235724857[154] = 0.0;
   out_2780673785235724857[155] = 0.0;
   out_2780673785235724857[156] = 0.0;
   out_2780673785235724857[157] = 0.0;
   out_2780673785235724857[158] = 0.0;
   out_2780673785235724857[159] = 0.0;
   out_2780673785235724857[160] = 0.0;
   out_2780673785235724857[161] = 0.0;
   out_2780673785235724857[162] = 0.0;
   out_2780673785235724857[163] = 0.0;
   out_2780673785235724857[164] = 0.0;
   out_2780673785235724857[165] = 0.0;
   out_2780673785235724857[166] = 0.0;
   out_2780673785235724857[167] = 0.0;
   out_2780673785235724857[168] = 0.0;
   out_2780673785235724857[169] = 0.0;
   out_2780673785235724857[170] = 0.0;
   out_2780673785235724857[171] = 1.0;
   out_2780673785235724857[172] = 0.0;
   out_2780673785235724857[173] = 0.0;
   out_2780673785235724857[174] = 0.0;
   out_2780673785235724857[175] = 0.0;
   out_2780673785235724857[176] = 0.0;
   out_2780673785235724857[177] = 0.0;
   out_2780673785235724857[178] = 0.0;
   out_2780673785235724857[179] = 0.0;
   out_2780673785235724857[180] = 0.0;
   out_2780673785235724857[181] = 0.0;
   out_2780673785235724857[182] = 0.0;
   out_2780673785235724857[183] = 0.0;
   out_2780673785235724857[184] = 0.0;
   out_2780673785235724857[185] = 0.0;
   out_2780673785235724857[186] = 0.0;
   out_2780673785235724857[187] = 0.0;
   out_2780673785235724857[188] = 0.0;
   out_2780673785235724857[189] = 0.0;
   out_2780673785235724857[190] = 1.0;
   out_2780673785235724857[191] = 0.0;
   out_2780673785235724857[192] = 0.0;
   out_2780673785235724857[193] = 0.0;
   out_2780673785235724857[194] = 0.0;
   out_2780673785235724857[195] = 0.0;
   out_2780673785235724857[196] = 0.0;
   out_2780673785235724857[197] = 0.0;
   out_2780673785235724857[198] = 0.0;
   out_2780673785235724857[199] = 0.0;
   out_2780673785235724857[200] = 0.0;
   out_2780673785235724857[201] = 0.0;
   out_2780673785235724857[202] = 0.0;
   out_2780673785235724857[203] = 0.0;
   out_2780673785235724857[204] = 0.0;
   out_2780673785235724857[205] = 0.0;
   out_2780673785235724857[206] = 0.0;
   out_2780673785235724857[207] = 0.0;
   out_2780673785235724857[208] = 0.0;
   out_2780673785235724857[209] = 1.0;
   out_2780673785235724857[210] = 0.0;
   out_2780673785235724857[211] = 0.0;
   out_2780673785235724857[212] = 0.0;
   out_2780673785235724857[213] = 0.0;
   out_2780673785235724857[214] = 0.0;
   out_2780673785235724857[215] = 0.0;
   out_2780673785235724857[216] = 0.0;
   out_2780673785235724857[217] = 0.0;
   out_2780673785235724857[218] = 0.0;
   out_2780673785235724857[219] = 0.0;
   out_2780673785235724857[220] = 0.0;
   out_2780673785235724857[221] = 0.0;
   out_2780673785235724857[222] = 0.0;
   out_2780673785235724857[223] = 0.0;
   out_2780673785235724857[224] = 0.0;
   out_2780673785235724857[225] = 0.0;
   out_2780673785235724857[226] = 0.0;
   out_2780673785235724857[227] = 0.0;
   out_2780673785235724857[228] = 1.0;
   out_2780673785235724857[229] = 0.0;
   out_2780673785235724857[230] = 0.0;
   out_2780673785235724857[231] = 0.0;
   out_2780673785235724857[232] = 0.0;
   out_2780673785235724857[233] = 0.0;
   out_2780673785235724857[234] = 0.0;
   out_2780673785235724857[235] = 0.0;
   out_2780673785235724857[236] = 0.0;
   out_2780673785235724857[237] = 0.0;
   out_2780673785235724857[238] = 0.0;
   out_2780673785235724857[239] = 0.0;
   out_2780673785235724857[240] = 0.0;
   out_2780673785235724857[241] = 0.0;
   out_2780673785235724857[242] = 0.0;
   out_2780673785235724857[243] = 0.0;
   out_2780673785235724857[244] = 0.0;
   out_2780673785235724857[245] = 0.0;
   out_2780673785235724857[246] = 0.0;
   out_2780673785235724857[247] = 1.0;
   out_2780673785235724857[248] = 0.0;
   out_2780673785235724857[249] = 0.0;
   out_2780673785235724857[250] = 0.0;
   out_2780673785235724857[251] = 0.0;
   out_2780673785235724857[252] = 0.0;
   out_2780673785235724857[253] = 0.0;
   out_2780673785235724857[254] = 0.0;
   out_2780673785235724857[255] = 0.0;
   out_2780673785235724857[256] = 0.0;
   out_2780673785235724857[257] = 0.0;
   out_2780673785235724857[258] = 0.0;
   out_2780673785235724857[259] = 0.0;
   out_2780673785235724857[260] = 0.0;
   out_2780673785235724857[261] = 0.0;
   out_2780673785235724857[262] = 0.0;
   out_2780673785235724857[263] = 0.0;
   out_2780673785235724857[264] = 0.0;
   out_2780673785235724857[265] = 0.0;
   out_2780673785235724857[266] = 1.0;
   out_2780673785235724857[267] = 0.0;
   out_2780673785235724857[268] = 0.0;
   out_2780673785235724857[269] = 0.0;
   out_2780673785235724857[270] = 0.0;
   out_2780673785235724857[271] = 0.0;
   out_2780673785235724857[272] = 0.0;
   out_2780673785235724857[273] = 0.0;
   out_2780673785235724857[274] = 0.0;
   out_2780673785235724857[275] = 0.0;
   out_2780673785235724857[276] = 0.0;
   out_2780673785235724857[277] = 0.0;
   out_2780673785235724857[278] = 0.0;
   out_2780673785235724857[279] = 0.0;
   out_2780673785235724857[280] = 0.0;
   out_2780673785235724857[281] = 0.0;
   out_2780673785235724857[282] = 0.0;
   out_2780673785235724857[283] = 0.0;
   out_2780673785235724857[284] = 0.0;
   out_2780673785235724857[285] = 1.0;
   out_2780673785235724857[286] = 0.0;
   out_2780673785235724857[287] = 0.0;
   out_2780673785235724857[288] = 0.0;
   out_2780673785235724857[289] = 0.0;
   out_2780673785235724857[290] = 0.0;
   out_2780673785235724857[291] = 0.0;
   out_2780673785235724857[292] = 0.0;
   out_2780673785235724857[293] = 0.0;
   out_2780673785235724857[294] = 0.0;
   out_2780673785235724857[295] = 0.0;
   out_2780673785235724857[296] = 0.0;
   out_2780673785235724857[297] = 0.0;
   out_2780673785235724857[298] = 0.0;
   out_2780673785235724857[299] = 0.0;
   out_2780673785235724857[300] = 0.0;
   out_2780673785235724857[301] = 0.0;
   out_2780673785235724857[302] = 0.0;
   out_2780673785235724857[303] = 0.0;
   out_2780673785235724857[304] = 1.0;
   out_2780673785235724857[305] = 0.0;
   out_2780673785235724857[306] = 0.0;
   out_2780673785235724857[307] = 0.0;
   out_2780673785235724857[308] = 0.0;
   out_2780673785235724857[309] = 0.0;
   out_2780673785235724857[310] = 0.0;
   out_2780673785235724857[311] = 0.0;
   out_2780673785235724857[312] = 0.0;
   out_2780673785235724857[313] = 0.0;
   out_2780673785235724857[314] = 0.0;
   out_2780673785235724857[315] = 0.0;
   out_2780673785235724857[316] = 0.0;
   out_2780673785235724857[317] = 0.0;
   out_2780673785235724857[318] = 0.0;
   out_2780673785235724857[319] = 0.0;
   out_2780673785235724857[320] = 0.0;
   out_2780673785235724857[321] = 0.0;
   out_2780673785235724857[322] = 0.0;
   out_2780673785235724857[323] = 1.0;
}
void f_fun(double *state, double dt, double *out_2987768017781358156) {
   out_2987768017781358156[0] = atan2((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), -(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]));
   out_2987768017781358156[1] = asin(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]));
   out_2987768017781358156[2] = atan2(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), -(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]));
   out_2987768017781358156[3] = dt*state[12] + state[3];
   out_2987768017781358156[4] = dt*state[13] + state[4];
   out_2987768017781358156[5] = dt*state[14] + state[5];
   out_2987768017781358156[6] = state[6];
   out_2987768017781358156[7] = state[7];
   out_2987768017781358156[8] = state[8];
   out_2987768017781358156[9] = state[9];
   out_2987768017781358156[10] = state[10];
   out_2987768017781358156[11] = state[11];
   out_2987768017781358156[12] = state[12];
   out_2987768017781358156[13] = state[13];
   out_2987768017781358156[14] = state[14];
   out_2987768017781358156[15] = state[15];
   out_2987768017781358156[16] = state[16];
   out_2987768017781358156[17] = state[17];
}
void F_fun(double *state, double dt, double *out_9215256453153547448) {
   out_9215256453153547448[0] = ((-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*cos(state[0])*cos(state[1]) - sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*cos(state[0])*cos(state[1]) - sin(dt*state[6])*sin(state[0])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_9215256453153547448[1] = ((-sin(dt*state[6])*sin(dt*state[8]) - sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*cos(state[1]) - (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*sin(state[1]) - sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(state[0]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*sin(state[1]) + (-sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) + sin(dt*state[8])*cos(dt*state[6]))*cos(state[1]) - sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(state[0]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_9215256453153547448[2] = 0;
   out_9215256453153547448[3] = 0;
   out_9215256453153547448[4] = 0;
   out_9215256453153547448[5] = 0;
   out_9215256453153547448[6] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(dt*cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) - dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_9215256453153547448[7] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*sin(dt*state[7])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[6])*sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) - dt*sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[7])*cos(dt*state[6])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[8])*sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]) - dt*sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_9215256453153547448[8] = ((dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((dt*sin(dt*state[6])*sin(dt*state[8]) + dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_9215256453153547448[9] = 0;
   out_9215256453153547448[10] = 0;
   out_9215256453153547448[11] = 0;
   out_9215256453153547448[12] = 0;
   out_9215256453153547448[13] = 0;
   out_9215256453153547448[14] = 0;
   out_9215256453153547448[15] = 0;
   out_9215256453153547448[16] = 0;
   out_9215256453153547448[17] = 0;
   out_9215256453153547448[18] = (-sin(dt*state[7])*sin(state[0])*cos(state[1]) - sin(dt*state[8])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_9215256453153547448[19] = (-sin(dt*state[7])*sin(state[1])*cos(state[0]) + sin(dt*state[8])*sin(state[0])*sin(state[1])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_9215256453153547448[20] = 0;
   out_9215256453153547448[21] = 0;
   out_9215256453153547448[22] = 0;
   out_9215256453153547448[23] = 0;
   out_9215256453153547448[24] = 0;
   out_9215256453153547448[25] = (dt*sin(dt*state[7])*sin(dt*state[8])*sin(state[0])*cos(state[1]) - dt*sin(dt*state[7])*sin(state[1])*cos(dt*state[8]) + dt*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_9215256453153547448[26] = (-dt*sin(dt*state[8])*sin(state[1])*cos(dt*state[7]) - dt*sin(state[0])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_9215256453153547448[27] = 0;
   out_9215256453153547448[28] = 0;
   out_9215256453153547448[29] = 0;
   out_9215256453153547448[30] = 0;
   out_9215256453153547448[31] = 0;
   out_9215256453153547448[32] = 0;
   out_9215256453153547448[33] = 0;
   out_9215256453153547448[34] = 0;
   out_9215256453153547448[35] = 0;
   out_9215256453153547448[36] = ((sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_9215256453153547448[37] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-sin(dt*state[7])*sin(state[2])*cos(state[0])*cos(state[1]) + sin(dt*state[8])*sin(state[0])*sin(state[2])*cos(dt*state[7])*cos(state[1]) - sin(state[1])*sin(state[2])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(-sin(dt*state[7])*cos(state[0])*cos(state[1])*cos(state[2]) + sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1])*cos(state[2]) - sin(state[1])*cos(dt*state[7])*cos(dt*state[8])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_9215256453153547448[38] = ((-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (-sin(state[0])*sin(state[1])*sin(state[2]) - cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_9215256453153547448[39] = 0;
   out_9215256453153547448[40] = 0;
   out_9215256453153547448[41] = 0;
   out_9215256453153547448[42] = 0;
   out_9215256453153547448[43] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(dt*(sin(state[0])*cos(state[2]) - sin(state[1])*sin(state[2])*cos(state[0]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*sin(state[2])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(dt*(-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_9215256453153547448[44] = (dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*sin(state[2])*cos(dt*state[7])*cos(state[1]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + (dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[7])*cos(state[1])*cos(state[2]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_9215256453153547448[45] = 0;
   out_9215256453153547448[46] = 0;
   out_9215256453153547448[47] = 0;
   out_9215256453153547448[48] = 0;
   out_9215256453153547448[49] = 0;
   out_9215256453153547448[50] = 0;
   out_9215256453153547448[51] = 0;
   out_9215256453153547448[52] = 0;
   out_9215256453153547448[53] = 0;
   out_9215256453153547448[54] = 0;
   out_9215256453153547448[55] = 0;
   out_9215256453153547448[56] = 0;
   out_9215256453153547448[57] = 1;
   out_9215256453153547448[58] = 0;
   out_9215256453153547448[59] = 0;
   out_9215256453153547448[60] = 0;
   out_9215256453153547448[61] = 0;
   out_9215256453153547448[62] = 0;
   out_9215256453153547448[63] = 0;
   out_9215256453153547448[64] = 0;
   out_9215256453153547448[65] = 0;
   out_9215256453153547448[66] = dt;
   out_9215256453153547448[67] = 0;
   out_9215256453153547448[68] = 0;
   out_9215256453153547448[69] = 0;
   out_9215256453153547448[70] = 0;
   out_9215256453153547448[71] = 0;
   out_9215256453153547448[72] = 0;
   out_9215256453153547448[73] = 0;
   out_9215256453153547448[74] = 0;
   out_9215256453153547448[75] = 0;
   out_9215256453153547448[76] = 1;
   out_9215256453153547448[77] = 0;
   out_9215256453153547448[78] = 0;
   out_9215256453153547448[79] = 0;
   out_9215256453153547448[80] = 0;
   out_9215256453153547448[81] = 0;
   out_9215256453153547448[82] = 0;
   out_9215256453153547448[83] = 0;
   out_9215256453153547448[84] = 0;
   out_9215256453153547448[85] = dt;
   out_9215256453153547448[86] = 0;
   out_9215256453153547448[87] = 0;
   out_9215256453153547448[88] = 0;
   out_9215256453153547448[89] = 0;
   out_9215256453153547448[90] = 0;
   out_9215256453153547448[91] = 0;
   out_9215256453153547448[92] = 0;
   out_9215256453153547448[93] = 0;
   out_9215256453153547448[94] = 0;
   out_9215256453153547448[95] = 1;
   out_9215256453153547448[96] = 0;
   out_9215256453153547448[97] = 0;
   out_9215256453153547448[98] = 0;
   out_9215256453153547448[99] = 0;
   out_9215256453153547448[100] = 0;
   out_9215256453153547448[101] = 0;
   out_9215256453153547448[102] = 0;
   out_9215256453153547448[103] = 0;
   out_9215256453153547448[104] = dt;
   out_9215256453153547448[105] = 0;
   out_9215256453153547448[106] = 0;
   out_9215256453153547448[107] = 0;
   out_9215256453153547448[108] = 0;
   out_9215256453153547448[109] = 0;
   out_9215256453153547448[110] = 0;
   out_9215256453153547448[111] = 0;
   out_9215256453153547448[112] = 0;
   out_9215256453153547448[113] = 0;
   out_9215256453153547448[114] = 1;
   out_9215256453153547448[115] = 0;
   out_9215256453153547448[116] = 0;
   out_9215256453153547448[117] = 0;
   out_9215256453153547448[118] = 0;
   out_9215256453153547448[119] = 0;
   out_9215256453153547448[120] = 0;
   out_9215256453153547448[121] = 0;
   out_9215256453153547448[122] = 0;
   out_9215256453153547448[123] = 0;
   out_9215256453153547448[124] = 0;
   out_9215256453153547448[125] = 0;
   out_9215256453153547448[126] = 0;
   out_9215256453153547448[127] = 0;
   out_9215256453153547448[128] = 0;
   out_9215256453153547448[129] = 0;
   out_9215256453153547448[130] = 0;
   out_9215256453153547448[131] = 0;
   out_9215256453153547448[132] = 0;
   out_9215256453153547448[133] = 1;
   out_9215256453153547448[134] = 0;
   out_9215256453153547448[135] = 0;
   out_9215256453153547448[136] = 0;
   out_9215256453153547448[137] = 0;
   out_9215256453153547448[138] = 0;
   out_9215256453153547448[139] = 0;
   out_9215256453153547448[140] = 0;
   out_9215256453153547448[141] = 0;
   out_9215256453153547448[142] = 0;
   out_9215256453153547448[143] = 0;
   out_9215256453153547448[144] = 0;
   out_9215256453153547448[145] = 0;
   out_9215256453153547448[146] = 0;
   out_9215256453153547448[147] = 0;
   out_9215256453153547448[148] = 0;
   out_9215256453153547448[149] = 0;
   out_9215256453153547448[150] = 0;
   out_9215256453153547448[151] = 0;
   out_9215256453153547448[152] = 1;
   out_9215256453153547448[153] = 0;
   out_9215256453153547448[154] = 0;
   out_9215256453153547448[155] = 0;
   out_9215256453153547448[156] = 0;
   out_9215256453153547448[157] = 0;
   out_9215256453153547448[158] = 0;
   out_9215256453153547448[159] = 0;
   out_9215256453153547448[160] = 0;
   out_9215256453153547448[161] = 0;
   out_9215256453153547448[162] = 0;
   out_9215256453153547448[163] = 0;
   out_9215256453153547448[164] = 0;
   out_9215256453153547448[165] = 0;
   out_9215256453153547448[166] = 0;
   out_9215256453153547448[167] = 0;
   out_9215256453153547448[168] = 0;
   out_9215256453153547448[169] = 0;
   out_9215256453153547448[170] = 0;
   out_9215256453153547448[171] = 1;
   out_9215256453153547448[172] = 0;
   out_9215256453153547448[173] = 0;
   out_9215256453153547448[174] = 0;
   out_9215256453153547448[175] = 0;
   out_9215256453153547448[176] = 0;
   out_9215256453153547448[177] = 0;
   out_9215256453153547448[178] = 0;
   out_9215256453153547448[179] = 0;
   out_9215256453153547448[180] = 0;
   out_9215256453153547448[181] = 0;
   out_9215256453153547448[182] = 0;
   out_9215256453153547448[183] = 0;
   out_9215256453153547448[184] = 0;
   out_9215256453153547448[185] = 0;
   out_9215256453153547448[186] = 0;
   out_9215256453153547448[187] = 0;
   out_9215256453153547448[188] = 0;
   out_9215256453153547448[189] = 0;
   out_9215256453153547448[190] = 1;
   out_9215256453153547448[191] = 0;
   out_9215256453153547448[192] = 0;
   out_9215256453153547448[193] = 0;
   out_9215256453153547448[194] = 0;
   out_9215256453153547448[195] = 0;
   out_9215256453153547448[196] = 0;
   out_9215256453153547448[197] = 0;
   out_9215256453153547448[198] = 0;
   out_9215256453153547448[199] = 0;
   out_9215256453153547448[200] = 0;
   out_9215256453153547448[201] = 0;
   out_9215256453153547448[202] = 0;
   out_9215256453153547448[203] = 0;
   out_9215256453153547448[204] = 0;
   out_9215256453153547448[205] = 0;
   out_9215256453153547448[206] = 0;
   out_9215256453153547448[207] = 0;
   out_9215256453153547448[208] = 0;
   out_9215256453153547448[209] = 1;
   out_9215256453153547448[210] = 0;
   out_9215256453153547448[211] = 0;
   out_9215256453153547448[212] = 0;
   out_9215256453153547448[213] = 0;
   out_9215256453153547448[214] = 0;
   out_9215256453153547448[215] = 0;
   out_9215256453153547448[216] = 0;
   out_9215256453153547448[217] = 0;
   out_9215256453153547448[218] = 0;
   out_9215256453153547448[219] = 0;
   out_9215256453153547448[220] = 0;
   out_9215256453153547448[221] = 0;
   out_9215256453153547448[222] = 0;
   out_9215256453153547448[223] = 0;
   out_9215256453153547448[224] = 0;
   out_9215256453153547448[225] = 0;
   out_9215256453153547448[226] = 0;
   out_9215256453153547448[227] = 0;
   out_9215256453153547448[228] = 1;
   out_9215256453153547448[229] = 0;
   out_9215256453153547448[230] = 0;
   out_9215256453153547448[231] = 0;
   out_9215256453153547448[232] = 0;
   out_9215256453153547448[233] = 0;
   out_9215256453153547448[234] = 0;
   out_9215256453153547448[235] = 0;
   out_9215256453153547448[236] = 0;
   out_9215256453153547448[237] = 0;
   out_9215256453153547448[238] = 0;
   out_9215256453153547448[239] = 0;
   out_9215256453153547448[240] = 0;
   out_9215256453153547448[241] = 0;
   out_9215256453153547448[242] = 0;
   out_9215256453153547448[243] = 0;
   out_9215256453153547448[244] = 0;
   out_9215256453153547448[245] = 0;
   out_9215256453153547448[246] = 0;
   out_9215256453153547448[247] = 1;
   out_9215256453153547448[248] = 0;
   out_9215256453153547448[249] = 0;
   out_9215256453153547448[250] = 0;
   out_9215256453153547448[251] = 0;
   out_9215256453153547448[252] = 0;
   out_9215256453153547448[253] = 0;
   out_9215256453153547448[254] = 0;
   out_9215256453153547448[255] = 0;
   out_9215256453153547448[256] = 0;
   out_9215256453153547448[257] = 0;
   out_9215256453153547448[258] = 0;
   out_9215256453153547448[259] = 0;
   out_9215256453153547448[260] = 0;
   out_9215256453153547448[261] = 0;
   out_9215256453153547448[262] = 0;
   out_9215256453153547448[263] = 0;
   out_9215256453153547448[264] = 0;
   out_9215256453153547448[265] = 0;
   out_9215256453153547448[266] = 1;
   out_9215256453153547448[267] = 0;
   out_9215256453153547448[268] = 0;
   out_9215256453153547448[269] = 0;
   out_9215256453153547448[270] = 0;
   out_9215256453153547448[271] = 0;
   out_9215256453153547448[272] = 0;
   out_9215256453153547448[273] = 0;
   out_9215256453153547448[274] = 0;
   out_9215256453153547448[275] = 0;
   out_9215256453153547448[276] = 0;
   out_9215256453153547448[277] = 0;
   out_9215256453153547448[278] = 0;
   out_9215256453153547448[279] = 0;
   out_9215256453153547448[280] = 0;
   out_9215256453153547448[281] = 0;
   out_9215256453153547448[282] = 0;
   out_9215256453153547448[283] = 0;
   out_9215256453153547448[284] = 0;
   out_9215256453153547448[285] = 1;
   out_9215256453153547448[286] = 0;
   out_9215256453153547448[287] = 0;
   out_9215256453153547448[288] = 0;
   out_9215256453153547448[289] = 0;
   out_9215256453153547448[290] = 0;
   out_9215256453153547448[291] = 0;
   out_9215256453153547448[292] = 0;
   out_9215256453153547448[293] = 0;
   out_9215256453153547448[294] = 0;
   out_9215256453153547448[295] = 0;
   out_9215256453153547448[296] = 0;
   out_9215256453153547448[297] = 0;
   out_9215256453153547448[298] = 0;
   out_9215256453153547448[299] = 0;
   out_9215256453153547448[300] = 0;
   out_9215256453153547448[301] = 0;
   out_9215256453153547448[302] = 0;
   out_9215256453153547448[303] = 0;
   out_9215256453153547448[304] = 1;
   out_9215256453153547448[305] = 0;
   out_9215256453153547448[306] = 0;
   out_9215256453153547448[307] = 0;
   out_9215256453153547448[308] = 0;
   out_9215256453153547448[309] = 0;
   out_9215256453153547448[310] = 0;
   out_9215256453153547448[311] = 0;
   out_9215256453153547448[312] = 0;
   out_9215256453153547448[313] = 0;
   out_9215256453153547448[314] = 0;
   out_9215256453153547448[315] = 0;
   out_9215256453153547448[316] = 0;
   out_9215256453153547448[317] = 0;
   out_9215256453153547448[318] = 0;
   out_9215256453153547448[319] = 0;
   out_9215256453153547448[320] = 0;
   out_9215256453153547448[321] = 0;
   out_9215256453153547448[322] = 0;
   out_9215256453153547448[323] = 1;
}
void h_4(double *state, double *unused, double *out_3412232203917073472) {
   out_3412232203917073472[0] = state[6] + state[9];
   out_3412232203917073472[1] = state[7] + state[10];
   out_3412232203917073472[2] = state[8] + state[11];
}
void H_4(double *state, double *unused, double *out_3534834787290437464) {
   out_3534834787290437464[0] = 0;
   out_3534834787290437464[1] = 0;
   out_3534834787290437464[2] = 0;
   out_3534834787290437464[3] = 0;
   out_3534834787290437464[4] = 0;
   out_3534834787290437464[5] = 0;
   out_3534834787290437464[6] = 1;
   out_3534834787290437464[7] = 0;
   out_3534834787290437464[8] = 0;
   out_3534834787290437464[9] = 1;
   out_3534834787290437464[10] = 0;
   out_3534834787290437464[11] = 0;
   out_3534834787290437464[12] = 0;
   out_3534834787290437464[13] = 0;
   out_3534834787290437464[14] = 0;
   out_3534834787290437464[15] = 0;
   out_3534834787290437464[16] = 0;
   out_3534834787290437464[17] = 0;
   out_3534834787290437464[18] = 0;
   out_3534834787290437464[19] = 0;
   out_3534834787290437464[20] = 0;
   out_3534834787290437464[21] = 0;
   out_3534834787290437464[22] = 0;
   out_3534834787290437464[23] = 0;
   out_3534834787290437464[24] = 0;
   out_3534834787290437464[25] = 1;
   out_3534834787290437464[26] = 0;
   out_3534834787290437464[27] = 0;
   out_3534834787290437464[28] = 1;
   out_3534834787290437464[29] = 0;
   out_3534834787290437464[30] = 0;
   out_3534834787290437464[31] = 0;
   out_3534834787290437464[32] = 0;
   out_3534834787290437464[33] = 0;
   out_3534834787290437464[34] = 0;
   out_3534834787290437464[35] = 0;
   out_3534834787290437464[36] = 0;
   out_3534834787290437464[37] = 0;
   out_3534834787290437464[38] = 0;
   out_3534834787290437464[39] = 0;
   out_3534834787290437464[40] = 0;
   out_3534834787290437464[41] = 0;
   out_3534834787290437464[42] = 0;
   out_3534834787290437464[43] = 0;
   out_3534834787290437464[44] = 1;
   out_3534834787290437464[45] = 0;
   out_3534834787290437464[46] = 0;
   out_3534834787290437464[47] = 1;
   out_3534834787290437464[48] = 0;
   out_3534834787290437464[49] = 0;
   out_3534834787290437464[50] = 0;
   out_3534834787290437464[51] = 0;
   out_3534834787290437464[52] = 0;
   out_3534834787290437464[53] = 0;
}
void h_10(double *state, double *unused, double *out_115890013071339636) {
   out_115890013071339636[0] = 9.8100000000000005*sin(state[1]) - state[4]*state[8] + state[5]*state[7] + state[12] + state[15];
   out_115890013071339636[1] = -9.8100000000000005*sin(state[0])*cos(state[1]) + state[3]*state[8] - state[5]*state[6] + state[13] + state[16];
   out_115890013071339636[2] = -9.8100000000000005*cos(state[0])*cos(state[1]) - state[3]*state[7] + state[4]*state[6] + state[14] + state[17];
}
void H_10(double *state, double *unused, double *out_6130958711041943396) {
   out_6130958711041943396[0] = 0;
   out_6130958711041943396[1] = 9.8100000000000005*cos(state[1]);
   out_6130958711041943396[2] = 0;
   out_6130958711041943396[3] = 0;
   out_6130958711041943396[4] = -state[8];
   out_6130958711041943396[5] = state[7];
   out_6130958711041943396[6] = 0;
   out_6130958711041943396[7] = state[5];
   out_6130958711041943396[8] = -state[4];
   out_6130958711041943396[9] = 0;
   out_6130958711041943396[10] = 0;
   out_6130958711041943396[11] = 0;
   out_6130958711041943396[12] = 1;
   out_6130958711041943396[13] = 0;
   out_6130958711041943396[14] = 0;
   out_6130958711041943396[15] = 1;
   out_6130958711041943396[16] = 0;
   out_6130958711041943396[17] = 0;
   out_6130958711041943396[18] = -9.8100000000000005*cos(state[0])*cos(state[1]);
   out_6130958711041943396[19] = 9.8100000000000005*sin(state[0])*sin(state[1]);
   out_6130958711041943396[20] = 0;
   out_6130958711041943396[21] = state[8];
   out_6130958711041943396[22] = 0;
   out_6130958711041943396[23] = -state[6];
   out_6130958711041943396[24] = -state[5];
   out_6130958711041943396[25] = 0;
   out_6130958711041943396[26] = state[3];
   out_6130958711041943396[27] = 0;
   out_6130958711041943396[28] = 0;
   out_6130958711041943396[29] = 0;
   out_6130958711041943396[30] = 0;
   out_6130958711041943396[31] = 1;
   out_6130958711041943396[32] = 0;
   out_6130958711041943396[33] = 0;
   out_6130958711041943396[34] = 1;
   out_6130958711041943396[35] = 0;
   out_6130958711041943396[36] = 9.8100000000000005*sin(state[0])*cos(state[1]);
   out_6130958711041943396[37] = 9.8100000000000005*sin(state[1])*cos(state[0]);
   out_6130958711041943396[38] = 0;
   out_6130958711041943396[39] = -state[7];
   out_6130958711041943396[40] = state[6];
   out_6130958711041943396[41] = 0;
   out_6130958711041943396[42] = state[4];
   out_6130958711041943396[43] = -state[3];
   out_6130958711041943396[44] = 0;
   out_6130958711041943396[45] = 0;
   out_6130958711041943396[46] = 0;
   out_6130958711041943396[47] = 0;
   out_6130958711041943396[48] = 0;
   out_6130958711041943396[49] = 0;
   out_6130958711041943396[50] = 1;
   out_6130958711041943396[51] = 0;
   out_6130958711041943396[52] = 0;
   out_6130958711041943396[53] = 1;
}
void h_13(double *state, double *unused, double *out_6371690278926638796) {
   out_6371690278926638796[0] = state[3];
   out_6371690278926638796[1] = state[4];
   out_6371690278926638796[2] = state[5];
}
void H_13(double *state, double *unused, double *out_7301278078102413223) {
   out_7301278078102413223[0] = 0;
   out_7301278078102413223[1] = 0;
   out_7301278078102413223[2] = 0;
   out_7301278078102413223[3] = 1;
   out_7301278078102413223[4] = 0;
   out_7301278078102413223[5] = 0;
   out_7301278078102413223[6] = 0;
   out_7301278078102413223[7] = 0;
   out_7301278078102413223[8] = 0;
   out_7301278078102413223[9] = 0;
   out_7301278078102413223[10] = 0;
   out_7301278078102413223[11] = 0;
   out_7301278078102413223[12] = 0;
   out_7301278078102413223[13] = 0;
   out_7301278078102413223[14] = 0;
   out_7301278078102413223[15] = 0;
   out_7301278078102413223[16] = 0;
   out_7301278078102413223[17] = 0;
   out_7301278078102413223[18] = 0;
   out_7301278078102413223[19] = 0;
   out_7301278078102413223[20] = 0;
   out_7301278078102413223[21] = 0;
   out_7301278078102413223[22] = 1;
   out_7301278078102413223[23] = 0;
   out_7301278078102413223[24] = 0;
   out_7301278078102413223[25] = 0;
   out_7301278078102413223[26] = 0;
   out_7301278078102413223[27] = 0;
   out_7301278078102413223[28] = 0;
   out_7301278078102413223[29] = 0;
   out_7301278078102413223[30] = 0;
   out_7301278078102413223[31] = 0;
   out_7301278078102413223[32] = 0;
   out_7301278078102413223[33] = 0;
   out_7301278078102413223[34] = 0;
   out_7301278078102413223[35] = 0;
   out_7301278078102413223[36] = 0;
   out_7301278078102413223[37] = 0;
   out_7301278078102413223[38] = 0;
   out_7301278078102413223[39] = 0;
   out_7301278078102413223[40] = 0;
   out_7301278078102413223[41] = 1;
   out_7301278078102413223[42] = 0;
   out_7301278078102413223[43] = 0;
   out_7301278078102413223[44] = 0;
   out_7301278078102413223[45] = 0;
   out_7301278078102413223[46] = 0;
   out_7301278078102413223[47] = 0;
   out_7301278078102413223[48] = 0;
   out_7301278078102413223[49] = 0;
   out_7301278078102413223[50] = 0;
   out_7301278078102413223[51] = 0;
   out_7301278078102413223[52] = 0;
   out_7301278078102413223[53] = 0;
}
void h_14(double *state, double *unused, double *out_6221009380571624691) {
   out_6221009380571624691[0] = state[6];
   out_6221009380571624691[1] = state[7];
   out_6221009380571624691[2] = state[8];
}
void H_14(double *state, double *unused, double *out_7498075643629921993) {
   out_7498075643629921993[0] = 0;
   out_7498075643629921993[1] = 0;
   out_7498075643629921993[2] = 0;
   out_7498075643629921993[3] = 0;
   out_7498075643629921993[4] = 0;
   out_7498075643629921993[5] = 0;
   out_7498075643629921993[6] = 1;
   out_7498075643629921993[7] = 0;
   out_7498075643629921993[8] = 0;
   out_7498075643629921993[9] = 0;
   out_7498075643629921993[10] = 0;
   out_7498075643629921993[11] = 0;
   out_7498075643629921993[12] = 0;
   out_7498075643629921993[13] = 0;
   out_7498075643629921993[14] = 0;
   out_7498075643629921993[15] = 0;
   out_7498075643629921993[16] = 0;
   out_7498075643629921993[17] = 0;
   out_7498075643629921993[18] = 0;
   out_7498075643629921993[19] = 0;
   out_7498075643629921993[20] = 0;
   out_7498075643629921993[21] = 0;
   out_7498075643629921993[22] = 0;
   out_7498075643629921993[23] = 0;
   out_7498075643629921993[24] = 0;
   out_7498075643629921993[25] = 1;
   out_7498075643629921993[26] = 0;
   out_7498075643629921993[27] = 0;
   out_7498075643629921993[28] = 0;
   out_7498075643629921993[29] = 0;
   out_7498075643629921993[30] = 0;
   out_7498075643629921993[31] = 0;
   out_7498075643629921993[32] = 0;
   out_7498075643629921993[33] = 0;
   out_7498075643629921993[34] = 0;
   out_7498075643629921993[35] = 0;
   out_7498075643629921993[36] = 0;
   out_7498075643629921993[37] = 0;
   out_7498075643629921993[38] = 0;
   out_7498075643629921993[39] = 0;
   out_7498075643629921993[40] = 0;
   out_7498075643629921993[41] = 0;
   out_7498075643629921993[42] = 0;
   out_7498075643629921993[43] = 0;
   out_7498075643629921993[44] = 1;
   out_7498075643629921993[45] = 0;
   out_7498075643629921993[46] = 0;
   out_7498075643629921993[47] = 0;
   out_7498075643629921993[48] = 0;
   out_7498075643629921993[49] = 0;
   out_7498075643629921993[50] = 0;
   out_7498075643629921993[51] = 0;
   out_7498075643629921993[52] = 0;
   out_7498075643629921993[53] = 0;
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
void pose_err_fun(double *nom_x, double *delta_x, double *out_4327736854274805720) {
  err_fun(nom_x, delta_x, out_4327736854274805720);
}
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_542433210393079092) {
  inv_err_fun(nom_x, true_x, out_542433210393079092);
}
void pose_H_mod_fun(double *state, double *out_2780673785235724857) {
  H_mod_fun(state, out_2780673785235724857);
}
void pose_f_fun(double *state, double dt, double *out_2987768017781358156) {
  f_fun(state,  dt, out_2987768017781358156);
}
void pose_F_fun(double *state, double dt, double *out_9215256453153547448) {
  F_fun(state,  dt, out_9215256453153547448);
}
void pose_h_4(double *state, double *unused, double *out_3412232203917073472) {
  h_4(state, unused, out_3412232203917073472);
}
void pose_H_4(double *state, double *unused, double *out_3534834787290437464) {
  H_4(state, unused, out_3534834787290437464);
}
void pose_h_10(double *state, double *unused, double *out_115890013071339636) {
  h_10(state, unused, out_115890013071339636);
}
void pose_H_10(double *state, double *unused, double *out_6130958711041943396) {
  H_10(state, unused, out_6130958711041943396);
}
void pose_h_13(double *state, double *unused, double *out_6371690278926638796) {
  h_13(state, unused, out_6371690278926638796);
}
void pose_H_13(double *state, double *unused, double *out_7301278078102413223) {
  H_13(state, unused, out_7301278078102413223);
}
void pose_h_14(double *state, double *unused, double *out_6221009380571624691) {
  h_14(state, unused, out_6221009380571624691);
}
void pose_H_14(double *state, double *unused, double *out_7498075643629921993) {
  H_14(state, unused, out_7498075643629921993);
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
