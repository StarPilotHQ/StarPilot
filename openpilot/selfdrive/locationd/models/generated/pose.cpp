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
void err_fun(double *nom_x, double *delta_x, double *out_8199567924068407102) {
   out_8199567924068407102[0] = delta_x[0] + nom_x[0];
   out_8199567924068407102[1] = delta_x[1] + nom_x[1];
   out_8199567924068407102[2] = delta_x[2] + nom_x[2];
   out_8199567924068407102[3] = delta_x[3] + nom_x[3];
   out_8199567924068407102[4] = delta_x[4] + nom_x[4];
   out_8199567924068407102[5] = delta_x[5] + nom_x[5];
   out_8199567924068407102[6] = delta_x[6] + nom_x[6];
   out_8199567924068407102[7] = delta_x[7] + nom_x[7];
   out_8199567924068407102[8] = delta_x[8] + nom_x[8];
   out_8199567924068407102[9] = delta_x[9] + nom_x[9];
   out_8199567924068407102[10] = delta_x[10] + nom_x[10];
   out_8199567924068407102[11] = delta_x[11] + nom_x[11];
   out_8199567924068407102[12] = delta_x[12] + nom_x[12];
   out_8199567924068407102[13] = delta_x[13] + nom_x[13];
   out_8199567924068407102[14] = delta_x[14] + nom_x[14];
   out_8199567924068407102[15] = delta_x[15] + nom_x[15];
   out_8199567924068407102[16] = delta_x[16] + nom_x[16];
   out_8199567924068407102[17] = delta_x[17] + nom_x[17];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_5268140790071390257) {
   out_5268140790071390257[0] = -nom_x[0] + true_x[0];
   out_5268140790071390257[1] = -nom_x[1] + true_x[1];
   out_5268140790071390257[2] = -nom_x[2] + true_x[2];
   out_5268140790071390257[3] = -nom_x[3] + true_x[3];
   out_5268140790071390257[4] = -nom_x[4] + true_x[4];
   out_5268140790071390257[5] = -nom_x[5] + true_x[5];
   out_5268140790071390257[6] = -nom_x[6] + true_x[6];
   out_5268140790071390257[7] = -nom_x[7] + true_x[7];
   out_5268140790071390257[8] = -nom_x[8] + true_x[8];
   out_5268140790071390257[9] = -nom_x[9] + true_x[9];
   out_5268140790071390257[10] = -nom_x[10] + true_x[10];
   out_5268140790071390257[11] = -nom_x[11] + true_x[11];
   out_5268140790071390257[12] = -nom_x[12] + true_x[12];
   out_5268140790071390257[13] = -nom_x[13] + true_x[13];
   out_5268140790071390257[14] = -nom_x[14] + true_x[14];
   out_5268140790071390257[15] = -nom_x[15] + true_x[15];
   out_5268140790071390257[16] = -nom_x[16] + true_x[16];
   out_5268140790071390257[17] = -nom_x[17] + true_x[17];
}
void H_mod_fun(double *state, double *out_1833265644165750071) {
   out_1833265644165750071[0] = 1.0;
   out_1833265644165750071[1] = 0.0;
   out_1833265644165750071[2] = 0.0;
   out_1833265644165750071[3] = 0.0;
   out_1833265644165750071[4] = 0.0;
   out_1833265644165750071[5] = 0.0;
   out_1833265644165750071[6] = 0.0;
   out_1833265644165750071[7] = 0.0;
   out_1833265644165750071[8] = 0.0;
   out_1833265644165750071[9] = 0.0;
   out_1833265644165750071[10] = 0.0;
   out_1833265644165750071[11] = 0.0;
   out_1833265644165750071[12] = 0.0;
   out_1833265644165750071[13] = 0.0;
   out_1833265644165750071[14] = 0.0;
   out_1833265644165750071[15] = 0.0;
   out_1833265644165750071[16] = 0.0;
   out_1833265644165750071[17] = 0.0;
   out_1833265644165750071[18] = 0.0;
   out_1833265644165750071[19] = 1.0;
   out_1833265644165750071[20] = 0.0;
   out_1833265644165750071[21] = 0.0;
   out_1833265644165750071[22] = 0.0;
   out_1833265644165750071[23] = 0.0;
   out_1833265644165750071[24] = 0.0;
   out_1833265644165750071[25] = 0.0;
   out_1833265644165750071[26] = 0.0;
   out_1833265644165750071[27] = 0.0;
   out_1833265644165750071[28] = 0.0;
   out_1833265644165750071[29] = 0.0;
   out_1833265644165750071[30] = 0.0;
   out_1833265644165750071[31] = 0.0;
   out_1833265644165750071[32] = 0.0;
   out_1833265644165750071[33] = 0.0;
   out_1833265644165750071[34] = 0.0;
   out_1833265644165750071[35] = 0.0;
   out_1833265644165750071[36] = 0.0;
   out_1833265644165750071[37] = 0.0;
   out_1833265644165750071[38] = 1.0;
   out_1833265644165750071[39] = 0.0;
   out_1833265644165750071[40] = 0.0;
   out_1833265644165750071[41] = 0.0;
   out_1833265644165750071[42] = 0.0;
   out_1833265644165750071[43] = 0.0;
   out_1833265644165750071[44] = 0.0;
   out_1833265644165750071[45] = 0.0;
   out_1833265644165750071[46] = 0.0;
   out_1833265644165750071[47] = 0.0;
   out_1833265644165750071[48] = 0.0;
   out_1833265644165750071[49] = 0.0;
   out_1833265644165750071[50] = 0.0;
   out_1833265644165750071[51] = 0.0;
   out_1833265644165750071[52] = 0.0;
   out_1833265644165750071[53] = 0.0;
   out_1833265644165750071[54] = 0.0;
   out_1833265644165750071[55] = 0.0;
   out_1833265644165750071[56] = 0.0;
   out_1833265644165750071[57] = 1.0;
   out_1833265644165750071[58] = 0.0;
   out_1833265644165750071[59] = 0.0;
   out_1833265644165750071[60] = 0.0;
   out_1833265644165750071[61] = 0.0;
   out_1833265644165750071[62] = 0.0;
   out_1833265644165750071[63] = 0.0;
   out_1833265644165750071[64] = 0.0;
   out_1833265644165750071[65] = 0.0;
   out_1833265644165750071[66] = 0.0;
   out_1833265644165750071[67] = 0.0;
   out_1833265644165750071[68] = 0.0;
   out_1833265644165750071[69] = 0.0;
   out_1833265644165750071[70] = 0.0;
   out_1833265644165750071[71] = 0.0;
   out_1833265644165750071[72] = 0.0;
   out_1833265644165750071[73] = 0.0;
   out_1833265644165750071[74] = 0.0;
   out_1833265644165750071[75] = 0.0;
   out_1833265644165750071[76] = 1.0;
   out_1833265644165750071[77] = 0.0;
   out_1833265644165750071[78] = 0.0;
   out_1833265644165750071[79] = 0.0;
   out_1833265644165750071[80] = 0.0;
   out_1833265644165750071[81] = 0.0;
   out_1833265644165750071[82] = 0.0;
   out_1833265644165750071[83] = 0.0;
   out_1833265644165750071[84] = 0.0;
   out_1833265644165750071[85] = 0.0;
   out_1833265644165750071[86] = 0.0;
   out_1833265644165750071[87] = 0.0;
   out_1833265644165750071[88] = 0.0;
   out_1833265644165750071[89] = 0.0;
   out_1833265644165750071[90] = 0.0;
   out_1833265644165750071[91] = 0.0;
   out_1833265644165750071[92] = 0.0;
   out_1833265644165750071[93] = 0.0;
   out_1833265644165750071[94] = 0.0;
   out_1833265644165750071[95] = 1.0;
   out_1833265644165750071[96] = 0.0;
   out_1833265644165750071[97] = 0.0;
   out_1833265644165750071[98] = 0.0;
   out_1833265644165750071[99] = 0.0;
   out_1833265644165750071[100] = 0.0;
   out_1833265644165750071[101] = 0.0;
   out_1833265644165750071[102] = 0.0;
   out_1833265644165750071[103] = 0.0;
   out_1833265644165750071[104] = 0.0;
   out_1833265644165750071[105] = 0.0;
   out_1833265644165750071[106] = 0.0;
   out_1833265644165750071[107] = 0.0;
   out_1833265644165750071[108] = 0.0;
   out_1833265644165750071[109] = 0.0;
   out_1833265644165750071[110] = 0.0;
   out_1833265644165750071[111] = 0.0;
   out_1833265644165750071[112] = 0.0;
   out_1833265644165750071[113] = 0.0;
   out_1833265644165750071[114] = 1.0;
   out_1833265644165750071[115] = 0.0;
   out_1833265644165750071[116] = 0.0;
   out_1833265644165750071[117] = 0.0;
   out_1833265644165750071[118] = 0.0;
   out_1833265644165750071[119] = 0.0;
   out_1833265644165750071[120] = 0.0;
   out_1833265644165750071[121] = 0.0;
   out_1833265644165750071[122] = 0.0;
   out_1833265644165750071[123] = 0.0;
   out_1833265644165750071[124] = 0.0;
   out_1833265644165750071[125] = 0.0;
   out_1833265644165750071[126] = 0.0;
   out_1833265644165750071[127] = 0.0;
   out_1833265644165750071[128] = 0.0;
   out_1833265644165750071[129] = 0.0;
   out_1833265644165750071[130] = 0.0;
   out_1833265644165750071[131] = 0.0;
   out_1833265644165750071[132] = 0.0;
   out_1833265644165750071[133] = 1.0;
   out_1833265644165750071[134] = 0.0;
   out_1833265644165750071[135] = 0.0;
   out_1833265644165750071[136] = 0.0;
   out_1833265644165750071[137] = 0.0;
   out_1833265644165750071[138] = 0.0;
   out_1833265644165750071[139] = 0.0;
   out_1833265644165750071[140] = 0.0;
   out_1833265644165750071[141] = 0.0;
   out_1833265644165750071[142] = 0.0;
   out_1833265644165750071[143] = 0.0;
   out_1833265644165750071[144] = 0.0;
   out_1833265644165750071[145] = 0.0;
   out_1833265644165750071[146] = 0.0;
   out_1833265644165750071[147] = 0.0;
   out_1833265644165750071[148] = 0.0;
   out_1833265644165750071[149] = 0.0;
   out_1833265644165750071[150] = 0.0;
   out_1833265644165750071[151] = 0.0;
   out_1833265644165750071[152] = 1.0;
   out_1833265644165750071[153] = 0.0;
   out_1833265644165750071[154] = 0.0;
   out_1833265644165750071[155] = 0.0;
   out_1833265644165750071[156] = 0.0;
   out_1833265644165750071[157] = 0.0;
   out_1833265644165750071[158] = 0.0;
   out_1833265644165750071[159] = 0.0;
   out_1833265644165750071[160] = 0.0;
   out_1833265644165750071[161] = 0.0;
   out_1833265644165750071[162] = 0.0;
   out_1833265644165750071[163] = 0.0;
   out_1833265644165750071[164] = 0.0;
   out_1833265644165750071[165] = 0.0;
   out_1833265644165750071[166] = 0.0;
   out_1833265644165750071[167] = 0.0;
   out_1833265644165750071[168] = 0.0;
   out_1833265644165750071[169] = 0.0;
   out_1833265644165750071[170] = 0.0;
   out_1833265644165750071[171] = 1.0;
   out_1833265644165750071[172] = 0.0;
   out_1833265644165750071[173] = 0.0;
   out_1833265644165750071[174] = 0.0;
   out_1833265644165750071[175] = 0.0;
   out_1833265644165750071[176] = 0.0;
   out_1833265644165750071[177] = 0.0;
   out_1833265644165750071[178] = 0.0;
   out_1833265644165750071[179] = 0.0;
   out_1833265644165750071[180] = 0.0;
   out_1833265644165750071[181] = 0.0;
   out_1833265644165750071[182] = 0.0;
   out_1833265644165750071[183] = 0.0;
   out_1833265644165750071[184] = 0.0;
   out_1833265644165750071[185] = 0.0;
   out_1833265644165750071[186] = 0.0;
   out_1833265644165750071[187] = 0.0;
   out_1833265644165750071[188] = 0.0;
   out_1833265644165750071[189] = 0.0;
   out_1833265644165750071[190] = 1.0;
   out_1833265644165750071[191] = 0.0;
   out_1833265644165750071[192] = 0.0;
   out_1833265644165750071[193] = 0.0;
   out_1833265644165750071[194] = 0.0;
   out_1833265644165750071[195] = 0.0;
   out_1833265644165750071[196] = 0.0;
   out_1833265644165750071[197] = 0.0;
   out_1833265644165750071[198] = 0.0;
   out_1833265644165750071[199] = 0.0;
   out_1833265644165750071[200] = 0.0;
   out_1833265644165750071[201] = 0.0;
   out_1833265644165750071[202] = 0.0;
   out_1833265644165750071[203] = 0.0;
   out_1833265644165750071[204] = 0.0;
   out_1833265644165750071[205] = 0.0;
   out_1833265644165750071[206] = 0.0;
   out_1833265644165750071[207] = 0.0;
   out_1833265644165750071[208] = 0.0;
   out_1833265644165750071[209] = 1.0;
   out_1833265644165750071[210] = 0.0;
   out_1833265644165750071[211] = 0.0;
   out_1833265644165750071[212] = 0.0;
   out_1833265644165750071[213] = 0.0;
   out_1833265644165750071[214] = 0.0;
   out_1833265644165750071[215] = 0.0;
   out_1833265644165750071[216] = 0.0;
   out_1833265644165750071[217] = 0.0;
   out_1833265644165750071[218] = 0.0;
   out_1833265644165750071[219] = 0.0;
   out_1833265644165750071[220] = 0.0;
   out_1833265644165750071[221] = 0.0;
   out_1833265644165750071[222] = 0.0;
   out_1833265644165750071[223] = 0.0;
   out_1833265644165750071[224] = 0.0;
   out_1833265644165750071[225] = 0.0;
   out_1833265644165750071[226] = 0.0;
   out_1833265644165750071[227] = 0.0;
   out_1833265644165750071[228] = 1.0;
   out_1833265644165750071[229] = 0.0;
   out_1833265644165750071[230] = 0.0;
   out_1833265644165750071[231] = 0.0;
   out_1833265644165750071[232] = 0.0;
   out_1833265644165750071[233] = 0.0;
   out_1833265644165750071[234] = 0.0;
   out_1833265644165750071[235] = 0.0;
   out_1833265644165750071[236] = 0.0;
   out_1833265644165750071[237] = 0.0;
   out_1833265644165750071[238] = 0.0;
   out_1833265644165750071[239] = 0.0;
   out_1833265644165750071[240] = 0.0;
   out_1833265644165750071[241] = 0.0;
   out_1833265644165750071[242] = 0.0;
   out_1833265644165750071[243] = 0.0;
   out_1833265644165750071[244] = 0.0;
   out_1833265644165750071[245] = 0.0;
   out_1833265644165750071[246] = 0.0;
   out_1833265644165750071[247] = 1.0;
   out_1833265644165750071[248] = 0.0;
   out_1833265644165750071[249] = 0.0;
   out_1833265644165750071[250] = 0.0;
   out_1833265644165750071[251] = 0.0;
   out_1833265644165750071[252] = 0.0;
   out_1833265644165750071[253] = 0.0;
   out_1833265644165750071[254] = 0.0;
   out_1833265644165750071[255] = 0.0;
   out_1833265644165750071[256] = 0.0;
   out_1833265644165750071[257] = 0.0;
   out_1833265644165750071[258] = 0.0;
   out_1833265644165750071[259] = 0.0;
   out_1833265644165750071[260] = 0.0;
   out_1833265644165750071[261] = 0.0;
   out_1833265644165750071[262] = 0.0;
   out_1833265644165750071[263] = 0.0;
   out_1833265644165750071[264] = 0.0;
   out_1833265644165750071[265] = 0.0;
   out_1833265644165750071[266] = 1.0;
   out_1833265644165750071[267] = 0.0;
   out_1833265644165750071[268] = 0.0;
   out_1833265644165750071[269] = 0.0;
   out_1833265644165750071[270] = 0.0;
   out_1833265644165750071[271] = 0.0;
   out_1833265644165750071[272] = 0.0;
   out_1833265644165750071[273] = 0.0;
   out_1833265644165750071[274] = 0.0;
   out_1833265644165750071[275] = 0.0;
   out_1833265644165750071[276] = 0.0;
   out_1833265644165750071[277] = 0.0;
   out_1833265644165750071[278] = 0.0;
   out_1833265644165750071[279] = 0.0;
   out_1833265644165750071[280] = 0.0;
   out_1833265644165750071[281] = 0.0;
   out_1833265644165750071[282] = 0.0;
   out_1833265644165750071[283] = 0.0;
   out_1833265644165750071[284] = 0.0;
   out_1833265644165750071[285] = 1.0;
   out_1833265644165750071[286] = 0.0;
   out_1833265644165750071[287] = 0.0;
   out_1833265644165750071[288] = 0.0;
   out_1833265644165750071[289] = 0.0;
   out_1833265644165750071[290] = 0.0;
   out_1833265644165750071[291] = 0.0;
   out_1833265644165750071[292] = 0.0;
   out_1833265644165750071[293] = 0.0;
   out_1833265644165750071[294] = 0.0;
   out_1833265644165750071[295] = 0.0;
   out_1833265644165750071[296] = 0.0;
   out_1833265644165750071[297] = 0.0;
   out_1833265644165750071[298] = 0.0;
   out_1833265644165750071[299] = 0.0;
   out_1833265644165750071[300] = 0.0;
   out_1833265644165750071[301] = 0.0;
   out_1833265644165750071[302] = 0.0;
   out_1833265644165750071[303] = 0.0;
   out_1833265644165750071[304] = 1.0;
   out_1833265644165750071[305] = 0.0;
   out_1833265644165750071[306] = 0.0;
   out_1833265644165750071[307] = 0.0;
   out_1833265644165750071[308] = 0.0;
   out_1833265644165750071[309] = 0.0;
   out_1833265644165750071[310] = 0.0;
   out_1833265644165750071[311] = 0.0;
   out_1833265644165750071[312] = 0.0;
   out_1833265644165750071[313] = 0.0;
   out_1833265644165750071[314] = 0.0;
   out_1833265644165750071[315] = 0.0;
   out_1833265644165750071[316] = 0.0;
   out_1833265644165750071[317] = 0.0;
   out_1833265644165750071[318] = 0.0;
   out_1833265644165750071[319] = 0.0;
   out_1833265644165750071[320] = 0.0;
   out_1833265644165750071[321] = 0.0;
   out_1833265644165750071[322] = 0.0;
   out_1833265644165750071[323] = 1.0;
}
void f_fun(double *state, double dt, double *out_584860365826166685) {
   out_584860365826166685[0] = atan2((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), -(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]));
   out_584860365826166685[1] = asin(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]));
   out_584860365826166685[2] = atan2(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), -(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]));
   out_584860365826166685[3] = dt*state[12] + state[3];
   out_584860365826166685[4] = dt*state[13] + state[4];
   out_584860365826166685[5] = dt*state[14] + state[5];
   out_584860365826166685[6] = state[6];
   out_584860365826166685[7] = state[7];
   out_584860365826166685[8] = state[8];
   out_584860365826166685[9] = state[9];
   out_584860365826166685[10] = state[10];
   out_584860365826166685[11] = state[11];
   out_584860365826166685[12] = state[12];
   out_584860365826166685[13] = state[13];
   out_584860365826166685[14] = state[14];
   out_584860365826166685[15] = state[15];
   out_584860365826166685[16] = state[16];
   out_584860365826166685[17] = state[17];
}
void F_fun(double *state, double dt, double *out_4197666271703963695) {
   out_4197666271703963695[0] = ((-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*cos(state[0])*cos(state[1]) - sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*cos(state[0])*cos(state[1]) - sin(dt*state[6])*sin(state[0])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_4197666271703963695[1] = ((-sin(dt*state[6])*sin(dt*state[8]) - sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*cos(state[1]) - (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*sin(state[1]) - sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(state[0]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*sin(state[1]) + (-sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) + sin(dt*state[8])*cos(dt*state[6]))*cos(state[1]) - sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(state[0]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_4197666271703963695[2] = 0;
   out_4197666271703963695[3] = 0;
   out_4197666271703963695[4] = 0;
   out_4197666271703963695[5] = 0;
   out_4197666271703963695[6] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(dt*cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) - dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_4197666271703963695[7] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*sin(dt*state[7])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[6])*sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) - dt*sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[7])*cos(dt*state[6])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[8])*sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]) - dt*sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_4197666271703963695[8] = ((dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((dt*sin(dt*state[6])*sin(dt*state[8]) + dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_4197666271703963695[9] = 0;
   out_4197666271703963695[10] = 0;
   out_4197666271703963695[11] = 0;
   out_4197666271703963695[12] = 0;
   out_4197666271703963695[13] = 0;
   out_4197666271703963695[14] = 0;
   out_4197666271703963695[15] = 0;
   out_4197666271703963695[16] = 0;
   out_4197666271703963695[17] = 0;
   out_4197666271703963695[18] = (-sin(dt*state[7])*sin(state[0])*cos(state[1]) - sin(dt*state[8])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_4197666271703963695[19] = (-sin(dt*state[7])*sin(state[1])*cos(state[0]) + sin(dt*state[8])*sin(state[0])*sin(state[1])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_4197666271703963695[20] = 0;
   out_4197666271703963695[21] = 0;
   out_4197666271703963695[22] = 0;
   out_4197666271703963695[23] = 0;
   out_4197666271703963695[24] = 0;
   out_4197666271703963695[25] = (dt*sin(dt*state[7])*sin(dt*state[8])*sin(state[0])*cos(state[1]) - dt*sin(dt*state[7])*sin(state[1])*cos(dt*state[8]) + dt*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_4197666271703963695[26] = (-dt*sin(dt*state[8])*sin(state[1])*cos(dt*state[7]) - dt*sin(state[0])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_4197666271703963695[27] = 0;
   out_4197666271703963695[28] = 0;
   out_4197666271703963695[29] = 0;
   out_4197666271703963695[30] = 0;
   out_4197666271703963695[31] = 0;
   out_4197666271703963695[32] = 0;
   out_4197666271703963695[33] = 0;
   out_4197666271703963695[34] = 0;
   out_4197666271703963695[35] = 0;
   out_4197666271703963695[36] = ((sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_4197666271703963695[37] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-sin(dt*state[7])*sin(state[2])*cos(state[0])*cos(state[1]) + sin(dt*state[8])*sin(state[0])*sin(state[2])*cos(dt*state[7])*cos(state[1]) - sin(state[1])*sin(state[2])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(-sin(dt*state[7])*cos(state[0])*cos(state[1])*cos(state[2]) + sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1])*cos(state[2]) - sin(state[1])*cos(dt*state[7])*cos(dt*state[8])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_4197666271703963695[38] = ((-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (-sin(state[0])*sin(state[1])*sin(state[2]) - cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_4197666271703963695[39] = 0;
   out_4197666271703963695[40] = 0;
   out_4197666271703963695[41] = 0;
   out_4197666271703963695[42] = 0;
   out_4197666271703963695[43] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(dt*(sin(state[0])*cos(state[2]) - sin(state[1])*sin(state[2])*cos(state[0]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*sin(state[2])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(dt*(-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_4197666271703963695[44] = (dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*sin(state[2])*cos(dt*state[7])*cos(state[1]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + (dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[7])*cos(state[1])*cos(state[2]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_4197666271703963695[45] = 0;
   out_4197666271703963695[46] = 0;
   out_4197666271703963695[47] = 0;
   out_4197666271703963695[48] = 0;
   out_4197666271703963695[49] = 0;
   out_4197666271703963695[50] = 0;
   out_4197666271703963695[51] = 0;
   out_4197666271703963695[52] = 0;
   out_4197666271703963695[53] = 0;
   out_4197666271703963695[54] = 0;
   out_4197666271703963695[55] = 0;
   out_4197666271703963695[56] = 0;
   out_4197666271703963695[57] = 1;
   out_4197666271703963695[58] = 0;
   out_4197666271703963695[59] = 0;
   out_4197666271703963695[60] = 0;
   out_4197666271703963695[61] = 0;
   out_4197666271703963695[62] = 0;
   out_4197666271703963695[63] = 0;
   out_4197666271703963695[64] = 0;
   out_4197666271703963695[65] = 0;
   out_4197666271703963695[66] = dt;
   out_4197666271703963695[67] = 0;
   out_4197666271703963695[68] = 0;
   out_4197666271703963695[69] = 0;
   out_4197666271703963695[70] = 0;
   out_4197666271703963695[71] = 0;
   out_4197666271703963695[72] = 0;
   out_4197666271703963695[73] = 0;
   out_4197666271703963695[74] = 0;
   out_4197666271703963695[75] = 0;
   out_4197666271703963695[76] = 1;
   out_4197666271703963695[77] = 0;
   out_4197666271703963695[78] = 0;
   out_4197666271703963695[79] = 0;
   out_4197666271703963695[80] = 0;
   out_4197666271703963695[81] = 0;
   out_4197666271703963695[82] = 0;
   out_4197666271703963695[83] = 0;
   out_4197666271703963695[84] = 0;
   out_4197666271703963695[85] = dt;
   out_4197666271703963695[86] = 0;
   out_4197666271703963695[87] = 0;
   out_4197666271703963695[88] = 0;
   out_4197666271703963695[89] = 0;
   out_4197666271703963695[90] = 0;
   out_4197666271703963695[91] = 0;
   out_4197666271703963695[92] = 0;
   out_4197666271703963695[93] = 0;
   out_4197666271703963695[94] = 0;
   out_4197666271703963695[95] = 1;
   out_4197666271703963695[96] = 0;
   out_4197666271703963695[97] = 0;
   out_4197666271703963695[98] = 0;
   out_4197666271703963695[99] = 0;
   out_4197666271703963695[100] = 0;
   out_4197666271703963695[101] = 0;
   out_4197666271703963695[102] = 0;
   out_4197666271703963695[103] = 0;
   out_4197666271703963695[104] = dt;
   out_4197666271703963695[105] = 0;
   out_4197666271703963695[106] = 0;
   out_4197666271703963695[107] = 0;
   out_4197666271703963695[108] = 0;
   out_4197666271703963695[109] = 0;
   out_4197666271703963695[110] = 0;
   out_4197666271703963695[111] = 0;
   out_4197666271703963695[112] = 0;
   out_4197666271703963695[113] = 0;
   out_4197666271703963695[114] = 1;
   out_4197666271703963695[115] = 0;
   out_4197666271703963695[116] = 0;
   out_4197666271703963695[117] = 0;
   out_4197666271703963695[118] = 0;
   out_4197666271703963695[119] = 0;
   out_4197666271703963695[120] = 0;
   out_4197666271703963695[121] = 0;
   out_4197666271703963695[122] = 0;
   out_4197666271703963695[123] = 0;
   out_4197666271703963695[124] = 0;
   out_4197666271703963695[125] = 0;
   out_4197666271703963695[126] = 0;
   out_4197666271703963695[127] = 0;
   out_4197666271703963695[128] = 0;
   out_4197666271703963695[129] = 0;
   out_4197666271703963695[130] = 0;
   out_4197666271703963695[131] = 0;
   out_4197666271703963695[132] = 0;
   out_4197666271703963695[133] = 1;
   out_4197666271703963695[134] = 0;
   out_4197666271703963695[135] = 0;
   out_4197666271703963695[136] = 0;
   out_4197666271703963695[137] = 0;
   out_4197666271703963695[138] = 0;
   out_4197666271703963695[139] = 0;
   out_4197666271703963695[140] = 0;
   out_4197666271703963695[141] = 0;
   out_4197666271703963695[142] = 0;
   out_4197666271703963695[143] = 0;
   out_4197666271703963695[144] = 0;
   out_4197666271703963695[145] = 0;
   out_4197666271703963695[146] = 0;
   out_4197666271703963695[147] = 0;
   out_4197666271703963695[148] = 0;
   out_4197666271703963695[149] = 0;
   out_4197666271703963695[150] = 0;
   out_4197666271703963695[151] = 0;
   out_4197666271703963695[152] = 1;
   out_4197666271703963695[153] = 0;
   out_4197666271703963695[154] = 0;
   out_4197666271703963695[155] = 0;
   out_4197666271703963695[156] = 0;
   out_4197666271703963695[157] = 0;
   out_4197666271703963695[158] = 0;
   out_4197666271703963695[159] = 0;
   out_4197666271703963695[160] = 0;
   out_4197666271703963695[161] = 0;
   out_4197666271703963695[162] = 0;
   out_4197666271703963695[163] = 0;
   out_4197666271703963695[164] = 0;
   out_4197666271703963695[165] = 0;
   out_4197666271703963695[166] = 0;
   out_4197666271703963695[167] = 0;
   out_4197666271703963695[168] = 0;
   out_4197666271703963695[169] = 0;
   out_4197666271703963695[170] = 0;
   out_4197666271703963695[171] = 1;
   out_4197666271703963695[172] = 0;
   out_4197666271703963695[173] = 0;
   out_4197666271703963695[174] = 0;
   out_4197666271703963695[175] = 0;
   out_4197666271703963695[176] = 0;
   out_4197666271703963695[177] = 0;
   out_4197666271703963695[178] = 0;
   out_4197666271703963695[179] = 0;
   out_4197666271703963695[180] = 0;
   out_4197666271703963695[181] = 0;
   out_4197666271703963695[182] = 0;
   out_4197666271703963695[183] = 0;
   out_4197666271703963695[184] = 0;
   out_4197666271703963695[185] = 0;
   out_4197666271703963695[186] = 0;
   out_4197666271703963695[187] = 0;
   out_4197666271703963695[188] = 0;
   out_4197666271703963695[189] = 0;
   out_4197666271703963695[190] = 1;
   out_4197666271703963695[191] = 0;
   out_4197666271703963695[192] = 0;
   out_4197666271703963695[193] = 0;
   out_4197666271703963695[194] = 0;
   out_4197666271703963695[195] = 0;
   out_4197666271703963695[196] = 0;
   out_4197666271703963695[197] = 0;
   out_4197666271703963695[198] = 0;
   out_4197666271703963695[199] = 0;
   out_4197666271703963695[200] = 0;
   out_4197666271703963695[201] = 0;
   out_4197666271703963695[202] = 0;
   out_4197666271703963695[203] = 0;
   out_4197666271703963695[204] = 0;
   out_4197666271703963695[205] = 0;
   out_4197666271703963695[206] = 0;
   out_4197666271703963695[207] = 0;
   out_4197666271703963695[208] = 0;
   out_4197666271703963695[209] = 1;
   out_4197666271703963695[210] = 0;
   out_4197666271703963695[211] = 0;
   out_4197666271703963695[212] = 0;
   out_4197666271703963695[213] = 0;
   out_4197666271703963695[214] = 0;
   out_4197666271703963695[215] = 0;
   out_4197666271703963695[216] = 0;
   out_4197666271703963695[217] = 0;
   out_4197666271703963695[218] = 0;
   out_4197666271703963695[219] = 0;
   out_4197666271703963695[220] = 0;
   out_4197666271703963695[221] = 0;
   out_4197666271703963695[222] = 0;
   out_4197666271703963695[223] = 0;
   out_4197666271703963695[224] = 0;
   out_4197666271703963695[225] = 0;
   out_4197666271703963695[226] = 0;
   out_4197666271703963695[227] = 0;
   out_4197666271703963695[228] = 1;
   out_4197666271703963695[229] = 0;
   out_4197666271703963695[230] = 0;
   out_4197666271703963695[231] = 0;
   out_4197666271703963695[232] = 0;
   out_4197666271703963695[233] = 0;
   out_4197666271703963695[234] = 0;
   out_4197666271703963695[235] = 0;
   out_4197666271703963695[236] = 0;
   out_4197666271703963695[237] = 0;
   out_4197666271703963695[238] = 0;
   out_4197666271703963695[239] = 0;
   out_4197666271703963695[240] = 0;
   out_4197666271703963695[241] = 0;
   out_4197666271703963695[242] = 0;
   out_4197666271703963695[243] = 0;
   out_4197666271703963695[244] = 0;
   out_4197666271703963695[245] = 0;
   out_4197666271703963695[246] = 0;
   out_4197666271703963695[247] = 1;
   out_4197666271703963695[248] = 0;
   out_4197666271703963695[249] = 0;
   out_4197666271703963695[250] = 0;
   out_4197666271703963695[251] = 0;
   out_4197666271703963695[252] = 0;
   out_4197666271703963695[253] = 0;
   out_4197666271703963695[254] = 0;
   out_4197666271703963695[255] = 0;
   out_4197666271703963695[256] = 0;
   out_4197666271703963695[257] = 0;
   out_4197666271703963695[258] = 0;
   out_4197666271703963695[259] = 0;
   out_4197666271703963695[260] = 0;
   out_4197666271703963695[261] = 0;
   out_4197666271703963695[262] = 0;
   out_4197666271703963695[263] = 0;
   out_4197666271703963695[264] = 0;
   out_4197666271703963695[265] = 0;
   out_4197666271703963695[266] = 1;
   out_4197666271703963695[267] = 0;
   out_4197666271703963695[268] = 0;
   out_4197666271703963695[269] = 0;
   out_4197666271703963695[270] = 0;
   out_4197666271703963695[271] = 0;
   out_4197666271703963695[272] = 0;
   out_4197666271703963695[273] = 0;
   out_4197666271703963695[274] = 0;
   out_4197666271703963695[275] = 0;
   out_4197666271703963695[276] = 0;
   out_4197666271703963695[277] = 0;
   out_4197666271703963695[278] = 0;
   out_4197666271703963695[279] = 0;
   out_4197666271703963695[280] = 0;
   out_4197666271703963695[281] = 0;
   out_4197666271703963695[282] = 0;
   out_4197666271703963695[283] = 0;
   out_4197666271703963695[284] = 0;
   out_4197666271703963695[285] = 1;
   out_4197666271703963695[286] = 0;
   out_4197666271703963695[287] = 0;
   out_4197666271703963695[288] = 0;
   out_4197666271703963695[289] = 0;
   out_4197666271703963695[290] = 0;
   out_4197666271703963695[291] = 0;
   out_4197666271703963695[292] = 0;
   out_4197666271703963695[293] = 0;
   out_4197666271703963695[294] = 0;
   out_4197666271703963695[295] = 0;
   out_4197666271703963695[296] = 0;
   out_4197666271703963695[297] = 0;
   out_4197666271703963695[298] = 0;
   out_4197666271703963695[299] = 0;
   out_4197666271703963695[300] = 0;
   out_4197666271703963695[301] = 0;
   out_4197666271703963695[302] = 0;
   out_4197666271703963695[303] = 0;
   out_4197666271703963695[304] = 1;
   out_4197666271703963695[305] = 0;
   out_4197666271703963695[306] = 0;
   out_4197666271703963695[307] = 0;
   out_4197666271703963695[308] = 0;
   out_4197666271703963695[309] = 0;
   out_4197666271703963695[310] = 0;
   out_4197666271703963695[311] = 0;
   out_4197666271703963695[312] = 0;
   out_4197666271703963695[313] = 0;
   out_4197666271703963695[314] = 0;
   out_4197666271703963695[315] = 0;
   out_4197666271703963695[316] = 0;
   out_4197666271703963695[317] = 0;
   out_4197666271703963695[318] = 0;
   out_4197666271703963695[319] = 0;
   out_4197666271703963695[320] = 0;
   out_4197666271703963695[321] = 0;
   out_4197666271703963695[322] = 0;
   out_4197666271703963695[323] = 1;
}
void h_4(double *state, double *unused, double *out_2182850352316805883) {
   out_2182850352316805883[0] = state[6] + state[9];
   out_2182850352316805883[1] = state[7] + state[10];
   out_2182850352316805883[2] = state[8] + state[11];
}
void H_4(double *state, double *unused, double *out_2781361392763566293) {
   out_2781361392763566293[0] = 0;
   out_2781361392763566293[1] = 0;
   out_2781361392763566293[2] = 0;
   out_2781361392763566293[3] = 0;
   out_2781361392763566293[4] = 0;
   out_2781361392763566293[5] = 0;
   out_2781361392763566293[6] = 1;
   out_2781361392763566293[7] = 0;
   out_2781361392763566293[8] = 0;
   out_2781361392763566293[9] = 1;
   out_2781361392763566293[10] = 0;
   out_2781361392763566293[11] = 0;
   out_2781361392763566293[12] = 0;
   out_2781361392763566293[13] = 0;
   out_2781361392763566293[14] = 0;
   out_2781361392763566293[15] = 0;
   out_2781361392763566293[16] = 0;
   out_2781361392763566293[17] = 0;
   out_2781361392763566293[18] = 0;
   out_2781361392763566293[19] = 0;
   out_2781361392763566293[20] = 0;
   out_2781361392763566293[21] = 0;
   out_2781361392763566293[22] = 0;
   out_2781361392763566293[23] = 0;
   out_2781361392763566293[24] = 0;
   out_2781361392763566293[25] = 1;
   out_2781361392763566293[26] = 0;
   out_2781361392763566293[27] = 0;
   out_2781361392763566293[28] = 1;
   out_2781361392763566293[29] = 0;
   out_2781361392763566293[30] = 0;
   out_2781361392763566293[31] = 0;
   out_2781361392763566293[32] = 0;
   out_2781361392763566293[33] = 0;
   out_2781361392763566293[34] = 0;
   out_2781361392763566293[35] = 0;
   out_2781361392763566293[36] = 0;
   out_2781361392763566293[37] = 0;
   out_2781361392763566293[38] = 0;
   out_2781361392763566293[39] = 0;
   out_2781361392763566293[40] = 0;
   out_2781361392763566293[41] = 0;
   out_2781361392763566293[42] = 0;
   out_2781361392763566293[43] = 0;
   out_2781361392763566293[44] = 1;
   out_2781361392763566293[45] = 0;
   out_2781361392763566293[46] = 0;
   out_2781361392763566293[47] = 1;
   out_2781361392763566293[48] = 0;
   out_2781361392763566293[49] = 0;
   out_2781361392763566293[50] = 0;
   out_2781361392763566293[51] = 0;
   out_2781361392763566293[52] = 0;
   out_2781361392763566293[53] = 0;
}
void h_10(double *state, double *unused, double *out_5529312285397171132) {
   out_5529312285397171132[0] = 9.8100000000000005*sin(state[1]) - state[4]*state[8] + state[5]*state[7] + state[12] + state[15];
   out_5529312285397171132[1] = -9.8100000000000005*sin(state[0])*cos(state[1]) + state[3]*state[8] - state[5]*state[6] + state[13] + state[16];
   out_5529312285397171132[2] = -9.8100000000000005*cos(state[0])*cos(state[1]) - state[3]*state[7] + state[4]*state[6] + state[14] + state[17];
}
void H_10(double *state, double *unused, double *out_8942930049609574379) {
   out_8942930049609574379[0] = 0;
   out_8942930049609574379[1] = 9.8100000000000005*cos(state[1]);
   out_8942930049609574379[2] = 0;
   out_8942930049609574379[3] = 0;
   out_8942930049609574379[4] = -state[8];
   out_8942930049609574379[5] = state[7];
   out_8942930049609574379[6] = 0;
   out_8942930049609574379[7] = state[5];
   out_8942930049609574379[8] = -state[4];
   out_8942930049609574379[9] = 0;
   out_8942930049609574379[10] = 0;
   out_8942930049609574379[11] = 0;
   out_8942930049609574379[12] = 1;
   out_8942930049609574379[13] = 0;
   out_8942930049609574379[14] = 0;
   out_8942930049609574379[15] = 1;
   out_8942930049609574379[16] = 0;
   out_8942930049609574379[17] = 0;
   out_8942930049609574379[18] = -9.8100000000000005*cos(state[0])*cos(state[1]);
   out_8942930049609574379[19] = 9.8100000000000005*sin(state[0])*sin(state[1]);
   out_8942930049609574379[20] = 0;
   out_8942930049609574379[21] = state[8];
   out_8942930049609574379[22] = 0;
   out_8942930049609574379[23] = -state[6];
   out_8942930049609574379[24] = -state[5];
   out_8942930049609574379[25] = 0;
   out_8942930049609574379[26] = state[3];
   out_8942930049609574379[27] = 0;
   out_8942930049609574379[28] = 0;
   out_8942930049609574379[29] = 0;
   out_8942930049609574379[30] = 0;
   out_8942930049609574379[31] = 1;
   out_8942930049609574379[32] = 0;
   out_8942930049609574379[33] = 0;
   out_8942930049609574379[34] = 1;
   out_8942930049609574379[35] = 0;
   out_8942930049609574379[36] = 9.8100000000000005*sin(state[0])*cos(state[1]);
   out_8942930049609574379[37] = 9.8100000000000005*sin(state[1])*cos(state[0]);
   out_8942930049609574379[38] = 0;
   out_8942930049609574379[39] = -state[7];
   out_8942930049609574379[40] = state[6];
   out_8942930049609574379[41] = 0;
   out_8942930049609574379[42] = state[4];
   out_8942930049609574379[43] = -state[3];
   out_8942930049609574379[44] = 0;
   out_8942930049609574379[45] = 0;
   out_8942930049609574379[46] = 0;
   out_8942930049609574379[47] = 0;
   out_8942930049609574379[48] = 0;
   out_8942930049609574379[49] = 0;
   out_8942930049609574379[50] = 1;
   out_8942930049609574379[51] = 0;
   out_8942930049609574379[52] = 0;
   out_8942930049609574379[53] = 1;
}
void h_13(double *state, double *unused, double *out_2548470441375331951) {
   out_2548470441375331951[0] = state[3];
   out_2548470441375331951[1] = state[4];
   out_2548470441375331951[2] = state[5];
}
void H_13(double *state, double *unused, double *out_5993635218095899094) {
   out_5993635218095899094[0] = 0;
   out_5993635218095899094[1] = 0;
   out_5993635218095899094[2] = 0;
   out_5993635218095899094[3] = 1;
   out_5993635218095899094[4] = 0;
   out_5993635218095899094[5] = 0;
   out_5993635218095899094[6] = 0;
   out_5993635218095899094[7] = 0;
   out_5993635218095899094[8] = 0;
   out_5993635218095899094[9] = 0;
   out_5993635218095899094[10] = 0;
   out_5993635218095899094[11] = 0;
   out_5993635218095899094[12] = 0;
   out_5993635218095899094[13] = 0;
   out_5993635218095899094[14] = 0;
   out_5993635218095899094[15] = 0;
   out_5993635218095899094[16] = 0;
   out_5993635218095899094[17] = 0;
   out_5993635218095899094[18] = 0;
   out_5993635218095899094[19] = 0;
   out_5993635218095899094[20] = 0;
   out_5993635218095899094[21] = 0;
   out_5993635218095899094[22] = 1;
   out_5993635218095899094[23] = 0;
   out_5993635218095899094[24] = 0;
   out_5993635218095899094[25] = 0;
   out_5993635218095899094[26] = 0;
   out_5993635218095899094[27] = 0;
   out_5993635218095899094[28] = 0;
   out_5993635218095899094[29] = 0;
   out_5993635218095899094[30] = 0;
   out_5993635218095899094[31] = 0;
   out_5993635218095899094[32] = 0;
   out_5993635218095899094[33] = 0;
   out_5993635218095899094[34] = 0;
   out_5993635218095899094[35] = 0;
   out_5993635218095899094[36] = 0;
   out_5993635218095899094[37] = 0;
   out_5993635218095899094[38] = 0;
   out_5993635218095899094[39] = 0;
   out_5993635218095899094[40] = 0;
   out_5993635218095899094[41] = 1;
   out_5993635218095899094[42] = 0;
   out_5993635218095899094[43] = 0;
   out_5993635218095899094[44] = 0;
   out_5993635218095899094[45] = 0;
   out_5993635218095899094[46] = 0;
   out_5993635218095899094[47] = 0;
   out_5993635218095899094[48] = 0;
   out_5993635218095899094[49] = 0;
   out_5993635218095899094[50] = 0;
   out_5993635218095899094[51] = 0;
   out_5993635218095899094[52] = 0;
   out_5993635218095899094[53] = 0;
}
void h_14(double *state, double *unused, double *out_2706566606486851807) {
   out_2706566606486851807[0] = state[6];
   out_2706566606486851807[1] = state[7];
   out_2706566606486851807[2] = state[8];
}
void H_14(double *state, double *unused, double *out_6744602249103050822) {
   out_6744602249103050822[0] = 0;
   out_6744602249103050822[1] = 0;
   out_6744602249103050822[2] = 0;
   out_6744602249103050822[3] = 0;
   out_6744602249103050822[4] = 0;
   out_6744602249103050822[5] = 0;
   out_6744602249103050822[6] = 1;
   out_6744602249103050822[7] = 0;
   out_6744602249103050822[8] = 0;
   out_6744602249103050822[9] = 0;
   out_6744602249103050822[10] = 0;
   out_6744602249103050822[11] = 0;
   out_6744602249103050822[12] = 0;
   out_6744602249103050822[13] = 0;
   out_6744602249103050822[14] = 0;
   out_6744602249103050822[15] = 0;
   out_6744602249103050822[16] = 0;
   out_6744602249103050822[17] = 0;
   out_6744602249103050822[18] = 0;
   out_6744602249103050822[19] = 0;
   out_6744602249103050822[20] = 0;
   out_6744602249103050822[21] = 0;
   out_6744602249103050822[22] = 0;
   out_6744602249103050822[23] = 0;
   out_6744602249103050822[24] = 0;
   out_6744602249103050822[25] = 1;
   out_6744602249103050822[26] = 0;
   out_6744602249103050822[27] = 0;
   out_6744602249103050822[28] = 0;
   out_6744602249103050822[29] = 0;
   out_6744602249103050822[30] = 0;
   out_6744602249103050822[31] = 0;
   out_6744602249103050822[32] = 0;
   out_6744602249103050822[33] = 0;
   out_6744602249103050822[34] = 0;
   out_6744602249103050822[35] = 0;
   out_6744602249103050822[36] = 0;
   out_6744602249103050822[37] = 0;
   out_6744602249103050822[38] = 0;
   out_6744602249103050822[39] = 0;
   out_6744602249103050822[40] = 0;
   out_6744602249103050822[41] = 0;
   out_6744602249103050822[42] = 0;
   out_6744602249103050822[43] = 0;
   out_6744602249103050822[44] = 1;
   out_6744602249103050822[45] = 0;
   out_6744602249103050822[46] = 0;
   out_6744602249103050822[47] = 0;
   out_6744602249103050822[48] = 0;
   out_6744602249103050822[49] = 0;
   out_6744602249103050822[50] = 0;
   out_6744602249103050822[51] = 0;
   out_6744602249103050822[52] = 0;
   out_6744602249103050822[53] = 0;
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
void pose_err_fun(double *nom_x, double *delta_x, double *out_8199567924068407102) {
  err_fun(nom_x, delta_x, out_8199567924068407102);
}
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_5268140790071390257) {
  inv_err_fun(nom_x, true_x, out_5268140790071390257);
}
void pose_H_mod_fun(double *state, double *out_1833265644165750071) {
  H_mod_fun(state, out_1833265644165750071);
}
void pose_f_fun(double *state, double dt, double *out_584860365826166685) {
  f_fun(state,  dt, out_584860365826166685);
}
void pose_F_fun(double *state, double dt, double *out_4197666271703963695) {
  F_fun(state,  dt, out_4197666271703963695);
}
void pose_h_4(double *state, double *unused, double *out_2182850352316805883) {
  h_4(state, unused, out_2182850352316805883);
}
void pose_H_4(double *state, double *unused, double *out_2781361392763566293) {
  H_4(state, unused, out_2781361392763566293);
}
void pose_h_10(double *state, double *unused, double *out_5529312285397171132) {
  h_10(state, unused, out_5529312285397171132);
}
void pose_H_10(double *state, double *unused, double *out_8942930049609574379) {
  H_10(state, unused, out_8942930049609574379);
}
void pose_h_13(double *state, double *unused, double *out_2548470441375331951) {
  h_13(state, unused, out_2548470441375331951);
}
void pose_H_13(double *state, double *unused, double *out_5993635218095899094) {
  H_13(state, unused, out_5993635218095899094);
}
void pose_h_14(double *state, double *unused, double *out_2706566606486851807) {
  h_14(state, unused, out_2706566606486851807);
}
void pose_H_14(double *state, double *unused, double *out_6744602249103050822) {
  H_14(state, unused, out_6744602249103050822);
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
