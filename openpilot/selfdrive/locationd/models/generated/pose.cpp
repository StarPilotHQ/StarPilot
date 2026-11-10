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
void err_fun(double *nom_x, double *delta_x, double *out_8357459543021008699) {
   out_8357459543021008699[0] = delta_x[0] + nom_x[0];
   out_8357459543021008699[1] = delta_x[1] + nom_x[1];
   out_8357459543021008699[2] = delta_x[2] + nom_x[2];
   out_8357459543021008699[3] = delta_x[3] + nom_x[3];
   out_8357459543021008699[4] = delta_x[4] + nom_x[4];
   out_8357459543021008699[5] = delta_x[5] + nom_x[5];
   out_8357459543021008699[6] = delta_x[6] + nom_x[6];
   out_8357459543021008699[7] = delta_x[7] + nom_x[7];
   out_8357459543021008699[8] = delta_x[8] + nom_x[8];
   out_8357459543021008699[9] = delta_x[9] + nom_x[9];
   out_8357459543021008699[10] = delta_x[10] + nom_x[10];
   out_8357459543021008699[11] = delta_x[11] + nom_x[11];
   out_8357459543021008699[12] = delta_x[12] + nom_x[12];
   out_8357459543021008699[13] = delta_x[13] + nom_x[13];
   out_8357459543021008699[14] = delta_x[14] + nom_x[14];
   out_8357459543021008699[15] = delta_x[15] + nom_x[15];
   out_8357459543021008699[16] = delta_x[16] + nom_x[16];
   out_8357459543021008699[17] = delta_x[17] + nom_x[17];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_3253386232645254795) {
   out_3253386232645254795[0] = -nom_x[0] + true_x[0];
   out_3253386232645254795[1] = -nom_x[1] + true_x[1];
   out_3253386232645254795[2] = -nom_x[2] + true_x[2];
   out_3253386232645254795[3] = -nom_x[3] + true_x[3];
   out_3253386232645254795[4] = -nom_x[4] + true_x[4];
   out_3253386232645254795[5] = -nom_x[5] + true_x[5];
   out_3253386232645254795[6] = -nom_x[6] + true_x[6];
   out_3253386232645254795[7] = -nom_x[7] + true_x[7];
   out_3253386232645254795[8] = -nom_x[8] + true_x[8];
   out_3253386232645254795[9] = -nom_x[9] + true_x[9];
   out_3253386232645254795[10] = -nom_x[10] + true_x[10];
   out_3253386232645254795[11] = -nom_x[11] + true_x[11];
   out_3253386232645254795[12] = -nom_x[12] + true_x[12];
   out_3253386232645254795[13] = -nom_x[13] + true_x[13];
   out_3253386232645254795[14] = -nom_x[14] + true_x[14];
   out_3253386232645254795[15] = -nom_x[15] + true_x[15];
   out_3253386232645254795[16] = -nom_x[16] + true_x[16];
   out_3253386232645254795[17] = -nom_x[17] + true_x[17];
}
void H_mod_fun(double *state, double *out_3551210960619195093) {
   out_3551210960619195093[0] = 1.0;
   out_3551210960619195093[1] = 0.0;
   out_3551210960619195093[2] = 0.0;
   out_3551210960619195093[3] = 0.0;
   out_3551210960619195093[4] = 0.0;
   out_3551210960619195093[5] = 0.0;
   out_3551210960619195093[6] = 0.0;
   out_3551210960619195093[7] = 0.0;
   out_3551210960619195093[8] = 0.0;
   out_3551210960619195093[9] = 0.0;
   out_3551210960619195093[10] = 0.0;
   out_3551210960619195093[11] = 0.0;
   out_3551210960619195093[12] = 0.0;
   out_3551210960619195093[13] = 0.0;
   out_3551210960619195093[14] = 0.0;
   out_3551210960619195093[15] = 0.0;
   out_3551210960619195093[16] = 0.0;
   out_3551210960619195093[17] = 0.0;
   out_3551210960619195093[18] = 0.0;
   out_3551210960619195093[19] = 1.0;
   out_3551210960619195093[20] = 0.0;
   out_3551210960619195093[21] = 0.0;
   out_3551210960619195093[22] = 0.0;
   out_3551210960619195093[23] = 0.0;
   out_3551210960619195093[24] = 0.0;
   out_3551210960619195093[25] = 0.0;
   out_3551210960619195093[26] = 0.0;
   out_3551210960619195093[27] = 0.0;
   out_3551210960619195093[28] = 0.0;
   out_3551210960619195093[29] = 0.0;
   out_3551210960619195093[30] = 0.0;
   out_3551210960619195093[31] = 0.0;
   out_3551210960619195093[32] = 0.0;
   out_3551210960619195093[33] = 0.0;
   out_3551210960619195093[34] = 0.0;
   out_3551210960619195093[35] = 0.0;
   out_3551210960619195093[36] = 0.0;
   out_3551210960619195093[37] = 0.0;
   out_3551210960619195093[38] = 1.0;
   out_3551210960619195093[39] = 0.0;
   out_3551210960619195093[40] = 0.0;
   out_3551210960619195093[41] = 0.0;
   out_3551210960619195093[42] = 0.0;
   out_3551210960619195093[43] = 0.0;
   out_3551210960619195093[44] = 0.0;
   out_3551210960619195093[45] = 0.0;
   out_3551210960619195093[46] = 0.0;
   out_3551210960619195093[47] = 0.0;
   out_3551210960619195093[48] = 0.0;
   out_3551210960619195093[49] = 0.0;
   out_3551210960619195093[50] = 0.0;
   out_3551210960619195093[51] = 0.0;
   out_3551210960619195093[52] = 0.0;
   out_3551210960619195093[53] = 0.0;
   out_3551210960619195093[54] = 0.0;
   out_3551210960619195093[55] = 0.0;
   out_3551210960619195093[56] = 0.0;
   out_3551210960619195093[57] = 1.0;
   out_3551210960619195093[58] = 0.0;
   out_3551210960619195093[59] = 0.0;
   out_3551210960619195093[60] = 0.0;
   out_3551210960619195093[61] = 0.0;
   out_3551210960619195093[62] = 0.0;
   out_3551210960619195093[63] = 0.0;
   out_3551210960619195093[64] = 0.0;
   out_3551210960619195093[65] = 0.0;
   out_3551210960619195093[66] = 0.0;
   out_3551210960619195093[67] = 0.0;
   out_3551210960619195093[68] = 0.0;
   out_3551210960619195093[69] = 0.0;
   out_3551210960619195093[70] = 0.0;
   out_3551210960619195093[71] = 0.0;
   out_3551210960619195093[72] = 0.0;
   out_3551210960619195093[73] = 0.0;
   out_3551210960619195093[74] = 0.0;
   out_3551210960619195093[75] = 0.0;
   out_3551210960619195093[76] = 1.0;
   out_3551210960619195093[77] = 0.0;
   out_3551210960619195093[78] = 0.0;
   out_3551210960619195093[79] = 0.0;
   out_3551210960619195093[80] = 0.0;
   out_3551210960619195093[81] = 0.0;
   out_3551210960619195093[82] = 0.0;
   out_3551210960619195093[83] = 0.0;
   out_3551210960619195093[84] = 0.0;
   out_3551210960619195093[85] = 0.0;
   out_3551210960619195093[86] = 0.0;
   out_3551210960619195093[87] = 0.0;
   out_3551210960619195093[88] = 0.0;
   out_3551210960619195093[89] = 0.0;
   out_3551210960619195093[90] = 0.0;
   out_3551210960619195093[91] = 0.0;
   out_3551210960619195093[92] = 0.0;
   out_3551210960619195093[93] = 0.0;
   out_3551210960619195093[94] = 0.0;
   out_3551210960619195093[95] = 1.0;
   out_3551210960619195093[96] = 0.0;
   out_3551210960619195093[97] = 0.0;
   out_3551210960619195093[98] = 0.0;
   out_3551210960619195093[99] = 0.0;
   out_3551210960619195093[100] = 0.0;
   out_3551210960619195093[101] = 0.0;
   out_3551210960619195093[102] = 0.0;
   out_3551210960619195093[103] = 0.0;
   out_3551210960619195093[104] = 0.0;
   out_3551210960619195093[105] = 0.0;
   out_3551210960619195093[106] = 0.0;
   out_3551210960619195093[107] = 0.0;
   out_3551210960619195093[108] = 0.0;
   out_3551210960619195093[109] = 0.0;
   out_3551210960619195093[110] = 0.0;
   out_3551210960619195093[111] = 0.0;
   out_3551210960619195093[112] = 0.0;
   out_3551210960619195093[113] = 0.0;
   out_3551210960619195093[114] = 1.0;
   out_3551210960619195093[115] = 0.0;
   out_3551210960619195093[116] = 0.0;
   out_3551210960619195093[117] = 0.0;
   out_3551210960619195093[118] = 0.0;
   out_3551210960619195093[119] = 0.0;
   out_3551210960619195093[120] = 0.0;
   out_3551210960619195093[121] = 0.0;
   out_3551210960619195093[122] = 0.0;
   out_3551210960619195093[123] = 0.0;
   out_3551210960619195093[124] = 0.0;
   out_3551210960619195093[125] = 0.0;
   out_3551210960619195093[126] = 0.0;
   out_3551210960619195093[127] = 0.0;
   out_3551210960619195093[128] = 0.0;
   out_3551210960619195093[129] = 0.0;
   out_3551210960619195093[130] = 0.0;
   out_3551210960619195093[131] = 0.0;
   out_3551210960619195093[132] = 0.0;
   out_3551210960619195093[133] = 1.0;
   out_3551210960619195093[134] = 0.0;
   out_3551210960619195093[135] = 0.0;
   out_3551210960619195093[136] = 0.0;
   out_3551210960619195093[137] = 0.0;
   out_3551210960619195093[138] = 0.0;
   out_3551210960619195093[139] = 0.0;
   out_3551210960619195093[140] = 0.0;
   out_3551210960619195093[141] = 0.0;
   out_3551210960619195093[142] = 0.0;
   out_3551210960619195093[143] = 0.0;
   out_3551210960619195093[144] = 0.0;
   out_3551210960619195093[145] = 0.0;
   out_3551210960619195093[146] = 0.0;
   out_3551210960619195093[147] = 0.0;
   out_3551210960619195093[148] = 0.0;
   out_3551210960619195093[149] = 0.0;
   out_3551210960619195093[150] = 0.0;
   out_3551210960619195093[151] = 0.0;
   out_3551210960619195093[152] = 1.0;
   out_3551210960619195093[153] = 0.0;
   out_3551210960619195093[154] = 0.0;
   out_3551210960619195093[155] = 0.0;
   out_3551210960619195093[156] = 0.0;
   out_3551210960619195093[157] = 0.0;
   out_3551210960619195093[158] = 0.0;
   out_3551210960619195093[159] = 0.0;
   out_3551210960619195093[160] = 0.0;
   out_3551210960619195093[161] = 0.0;
   out_3551210960619195093[162] = 0.0;
   out_3551210960619195093[163] = 0.0;
   out_3551210960619195093[164] = 0.0;
   out_3551210960619195093[165] = 0.0;
   out_3551210960619195093[166] = 0.0;
   out_3551210960619195093[167] = 0.0;
   out_3551210960619195093[168] = 0.0;
   out_3551210960619195093[169] = 0.0;
   out_3551210960619195093[170] = 0.0;
   out_3551210960619195093[171] = 1.0;
   out_3551210960619195093[172] = 0.0;
   out_3551210960619195093[173] = 0.0;
   out_3551210960619195093[174] = 0.0;
   out_3551210960619195093[175] = 0.0;
   out_3551210960619195093[176] = 0.0;
   out_3551210960619195093[177] = 0.0;
   out_3551210960619195093[178] = 0.0;
   out_3551210960619195093[179] = 0.0;
   out_3551210960619195093[180] = 0.0;
   out_3551210960619195093[181] = 0.0;
   out_3551210960619195093[182] = 0.0;
   out_3551210960619195093[183] = 0.0;
   out_3551210960619195093[184] = 0.0;
   out_3551210960619195093[185] = 0.0;
   out_3551210960619195093[186] = 0.0;
   out_3551210960619195093[187] = 0.0;
   out_3551210960619195093[188] = 0.0;
   out_3551210960619195093[189] = 0.0;
   out_3551210960619195093[190] = 1.0;
   out_3551210960619195093[191] = 0.0;
   out_3551210960619195093[192] = 0.0;
   out_3551210960619195093[193] = 0.0;
   out_3551210960619195093[194] = 0.0;
   out_3551210960619195093[195] = 0.0;
   out_3551210960619195093[196] = 0.0;
   out_3551210960619195093[197] = 0.0;
   out_3551210960619195093[198] = 0.0;
   out_3551210960619195093[199] = 0.0;
   out_3551210960619195093[200] = 0.0;
   out_3551210960619195093[201] = 0.0;
   out_3551210960619195093[202] = 0.0;
   out_3551210960619195093[203] = 0.0;
   out_3551210960619195093[204] = 0.0;
   out_3551210960619195093[205] = 0.0;
   out_3551210960619195093[206] = 0.0;
   out_3551210960619195093[207] = 0.0;
   out_3551210960619195093[208] = 0.0;
   out_3551210960619195093[209] = 1.0;
   out_3551210960619195093[210] = 0.0;
   out_3551210960619195093[211] = 0.0;
   out_3551210960619195093[212] = 0.0;
   out_3551210960619195093[213] = 0.0;
   out_3551210960619195093[214] = 0.0;
   out_3551210960619195093[215] = 0.0;
   out_3551210960619195093[216] = 0.0;
   out_3551210960619195093[217] = 0.0;
   out_3551210960619195093[218] = 0.0;
   out_3551210960619195093[219] = 0.0;
   out_3551210960619195093[220] = 0.0;
   out_3551210960619195093[221] = 0.0;
   out_3551210960619195093[222] = 0.0;
   out_3551210960619195093[223] = 0.0;
   out_3551210960619195093[224] = 0.0;
   out_3551210960619195093[225] = 0.0;
   out_3551210960619195093[226] = 0.0;
   out_3551210960619195093[227] = 0.0;
   out_3551210960619195093[228] = 1.0;
   out_3551210960619195093[229] = 0.0;
   out_3551210960619195093[230] = 0.0;
   out_3551210960619195093[231] = 0.0;
   out_3551210960619195093[232] = 0.0;
   out_3551210960619195093[233] = 0.0;
   out_3551210960619195093[234] = 0.0;
   out_3551210960619195093[235] = 0.0;
   out_3551210960619195093[236] = 0.0;
   out_3551210960619195093[237] = 0.0;
   out_3551210960619195093[238] = 0.0;
   out_3551210960619195093[239] = 0.0;
   out_3551210960619195093[240] = 0.0;
   out_3551210960619195093[241] = 0.0;
   out_3551210960619195093[242] = 0.0;
   out_3551210960619195093[243] = 0.0;
   out_3551210960619195093[244] = 0.0;
   out_3551210960619195093[245] = 0.0;
   out_3551210960619195093[246] = 0.0;
   out_3551210960619195093[247] = 1.0;
   out_3551210960619195093[248] = 0.0;
   out_3551210960619195093[249] = 0.0;
   out_3551210960619195093[250] = 0.0;
   out_3551210960619195093[251] = 0.0;
   out_3551210960619195093[252] = 0.0;
   out_3551210960619195093[253] = 0.0;
   out_3551210960619195093[254] = 0.0;
   out_3551210960619195093[255] = 0.0;
   out_3551210960619195093[256] = 0.0;
   out_3551210960619195093[257] = 0.0;
   out_3551210960619195093[258] = 0.0;
   out_3551210960619195093[259] = 0.0;
   out_3551210960619195093[260] = 0.0;
   out_3551210960619195093[261] = 0.0;
   out_3551210960619195093[262] = 0.0;
   out_3551210960619195093[263] = 0.0;
   out_3551210960619195093[264] = 0.0;
   out_3551210960619195093[265] = 0.0;
   out_3551210960619195093[266] = 1.0;
   out_3551210960619195093[267] = 0.0;
   out_3551210960619195093[268] = 0.0;
   out_3551210960619195093[269] = 0.0;
   out_3551210960619195093[270] = 0.0;
   out_3551210960619195093[271] = 0.0;
   out_3551210960619195093[272] = 0.0;
   out_3551210960619195093[273] = 0.0;
   out_3551210960619195093[274] = 0.0;
   out_3551210960619195093[275] = 0.0;
   out_3551210960619195093[276] = 0.0;
   out_3551210960619195093[277] = 0.0;
   out_3551210960619195093[278] = 0.0;
   out_3551210960619195093[279] = 0.0;
   out_3551210960619195093[280] = 0.0;
   out_3551210960619195093[281] = 0.0;
   out_3551210960619195093[282] = 0.0;
   out_3551210960619195093[283] = 0.0;
   out_3551210960619195093[284] = 0.0;
   out_3551210960619195093[285] = 1.0;
   out_3551210960619195093[286] = 0.0;
   out_3551210960619195093[287] = 0.0;
   out_3551210960619195093[288] = 0.0;
   out_3551210960619195093[289] = 0.0;
   out_3551210960619195093[290] = 0.0;
   out_3551210960619195093[291] = 0.0;
   out_3551210960619195093[292] = 0.0;
   out_3551210960619195093[293] = 0.0;
   out_3551210960619195093[294] = 0.0;
   out_3551210960619195093[295] = 0.0;
   out_3551210960619195093[296] = 0.0;
   out_3551210960619195093[297] = 0.0;
   out_3551210960619195093[298] = 0.0;
   out_3551210960619195093[299] = 0.0;
   out_3551210960619195093[300] = 0.0;
   out_3551210960619195093[301] = 0.0;
   out_3551210960619195093[302] = 0.0;
   out_3551210960619195093[303] = 0.0;
   out_3551210960619195093[304] = 1.0;
   out_3551210960619195093[305] = 0.0;
   out_3551210960619195093[306] = 0.0;
   out_3551210960619195093[307] = 0.0;
   out_3551210960619195093[308] = 0.0;
   out_3551210960619195093[309] = 0.0;
   out_3551210960619195093[310] = 0.0;
   out_3551210960619195093[311] = 0.0;
   out_3551210960619195093[312] = 0.0;
   out_3551210960619195093[313] = 0.0;
   out_3551210960619195093[314] = 0.0;
   out_3551210960619195093[315] = 0.0;
   out_3551210960619195093[316] = 0.0;
   out_3551210960619195093[317] = 0.0;
   out_3551210960619195093[318] = 0.0;
   out_3551210960619195093[319] = 0.0;
   out_3551210960619195093[320] = 0.0;
   out_3551210960619195093[321] = 0.0;
   out_3551210960619195093[322] = 0.0;
   out_3551210960619195093[323] = 1.0;
}
void f_fun(double *state, double dt, double *out_3486488515702872216) {
   out_3486488515702872216[0] = atan2((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), -(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]));
   out_3486488515702872216[1] = asin(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]));
   out_3486488515702872216[2] = atan2(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), -(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]));
   out_3486488515702872216[3] = dt*state[12] + state[3];
   out_3486488515702872216[4] = dt*state[13] + state[4];
   out_3486488515702872216[5] = dt*state[14] + state[5];
   out_3486488515702872216[6] = state[6];
   out_3486488515702872216[7] = state[7];
   out_3486488515702872216[8] = state[8];
   out_3486488515702872216[9] = state[9];
   out_3486488515702872216[10] = state[10];
   out_3486488515702872216[11] = state[11];
   out_3486488515702872216[12] = state[12];
   out_3486488515702872216[13] = state[13];
   out_3486488515702872216[14] = state[14];
   out_3486488515702872216[15] = state[15];
   out_3486488515702872216[16] = state[16];
   out_3486488515702872216[17] = state[17];
}
void F_fun(double *state, double dt, double *out_6504624075581041956) {
   out_6504624075581041956[0] = ((-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*cos(state[0])*cos(state[1]) - sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*cos(state[0])*cos(state[1]) - sin(dt*state[6])*sin(state[0])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_6504624075581041956[1] = ((-sin(dt*state[6])*sin(dt*state[8]) - sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*cos(state[1]) - (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*sin(state[1]) - sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(state[0]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*sin(state[1]) + (-sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) + sin(dt*state[8])*cos(dt*state[6]))*cos(state[1]) - sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(state[0]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_6504624075581041956[2] = 0;
   out_6504624075581041956[3] = 0;
   out_6504624075581041956[4] = 0;
   out_6504624075581041956[5] = 0;
   out_6504624075581041956[6] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(dt*cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) - dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_6504624075581041956[7] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*sin(dt*state[7])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[6])*sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) - dt*sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[7])*cos(dt*state[6])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[8])*sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]) - dt*sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_6504624075581041956[8] = ((dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((dt*sin(dt*state[6])*sin(dt*state[8]) + dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_6504624075581041956[9] = 0;
   out_6504624075581041956[10] = 0;
   out_6504624075581041956[11] = 0;
   out_6504624075581041956[12] = 0;
   out_6504624075581041956[13] = 0;
   out_6504624075581041956[14] = 0;
   out_6504624075581041956[15] = 0;
   out_6504624075581041956[16] = 0;
   out_6504624075581041956[17] = 0;
   out_6504624075581041956[18] = (-sin(dt*state[7])*sin(state[0])*cos(state[1]) - sin(dt*state[8])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_6504624075581041956[19] = (-sin(dt*state[7])*sin(state[1])*cos(state[0]) + sin(dt*state[8])*sin(state[0])*sin(state[1])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_6504624075581041956[20] = 0;
   out_6504624075581041956[21] = 0;
   out_6504624075581041956[22] = 0;
   out_6504624075581041956[23] = 0;
   out_6504624075581041956[24] = 0;
   out_6504624075581041956[25] = (dt*sin(dt*state[7])*sin(dt*state[8])*sin(state[0])*cos(state[1]) - dt*sin(dt*state[7])*sin(state[1])*cos(dt*state[8]) + dt*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_6504624075581041956[26] = (-dt*sin(dt*state[8])*sin(state[1])*cos(dt*state[7]) - dt*sin(state[0])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_6504624075581041956[27] = 0;
   out_6504624075581041956[28] = 0;
   out_6504624075581041956[29] = 0;
   out_6504624075581041956[30] = 0;
   out_6504624075581041956[31] = 0;
   out_6504624075581041956[32] = 0;
   out_6504624075581041956[33] = 0;
   out_6504624075581041956[34] = 0;
   out_6504624075581041956[35] = 0;
   out_6504624075581041956[36] = ((sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_6504624075581041956[37] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-sin(dt*state[7])*sin(state[2])*cos(state[0])*cos(state[1]) + sin(dt*state[8])*sin(state[0])*sin(state[2])*cos(dt*state[7])*cos(state[1]) - sin(state[1])*sin(state[2])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(-sin(dt*state[7])*cos(state[0])*cos(state[1])*cos(state[2]) + sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1])*cos(state[2]) - sin(state[1])*cos(dt*state[7])*cos(dt*state[8])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_6504624075581041956[38] = ((-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (-sin(state[0])*sin(state[1])*sin(state[2]) - cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_6504624075581041956[39] = 0;
   out_6504624075581041956[40] = 0;
   out_6504624075581041956[41] = 0;
   out_6504624075581041956[42] = 0;
   out_6504624075581041956[43] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(dt*(sin(state[0])*cos(state[2]) - sin(state[1])*sin(state[2])*cos(state[0]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*sin(state[2])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(dt*(-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_6504624075581041956[44] = (dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*sin(state[2])*cos(dt*state[7])*cos(state[1]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + (dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[7])*cos(state[1])*cos(state[2]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_6504624075581041956[45] = 0;
   out_6504624075581041956[46] = 0;
   out_6504624075581041956[47] = 0;
   out_6504624075581041956[48] = 0;
   out_6504624075581041956[49] = 0;
   out_6504624075581041956[50] = 0;
   out_6504624075581041956[51] = 0;
   out_6504624075581041956[52] = 0;
   out_6504624075581041956[53] = 0;
   out_6504624075581041956[54] = 0;
   out_6504624075581041956[55] = 0;
   out_6504624075581041956[56] = 0;
   out_6504624075581041956[57] = 1;
   out_6504624075581041956[58] = 0;
   out_6504624075581041956[59] = 0;
   out_6504624075581041956[60] = 0;
   out_6504624075581041956[61] = 0;
   out_6504624075581041956[62] = 0;
   out_6504624075581041956[63] = 0;
   out_6504624075581041956[64] = 0;
   out_6504624075581041956[65] = 0;
   out_6504624075581041956[66] = dt;
   out_6504624075581041956[67] = 0;
   out_6504624075581041956[68] = 0;
   out_6504624075581041956[69] = 0;
   out_6504624075581041956[70] = 0;
   out_6504624075581041956[71] = 0;
   out_6504624075581041956[72] = 0;
   out_6504624075581041956[73] = 0;
   out_6504624075581041956[74] = 0;
   out_6504624075581041956[75] = 0;
   out_6504624075581041956[76] = 1;
   out_6504624075581041956[77] = 0;
   out_6504624075581041956[78] = 0;
   out_6504624075581041956[79] = 0;
   out_6504624075581041956[80] = 0;
   out_6504624075581041956[81] = 0;
   out_6504624075581041956[82] = 0;
   out_6504624075581041956[83] = 0;
   out_6504624075581041956[84] = 0;
   out_6504624075581041956[85] = dt;
   out_6504624075581041956[86] = 0;
   out_6504624075581041956[87] = 0;
   out_6504624075581041956[88] = 0;
   out_6504624075581041956[89] = 0;
   out_6504624075581041956[90] = 0;
   out_6504624075581041956[91] = 0;
   out_6504624075581041956[92] = 0;
   out_6504624075581041956[93] = 0;
   out_6504624075581041956[94] = 0;
   out_6504624075581041956[95] = 1;
   out_6504624075581041956[96] = 0;
   out_6504624075581041956[97] = 0;
   out_6504624075581041956[98] = 0;
   out_6504624075581041956[99] = 0;
   out_6504624075581041956[100] = 0;
   out_6504624075581041956[101] = 0;
   out_6504624075581041956[102] = 0;
   out_6504624075581041956[103] = 0;
   out_6504624075581041956[104] = dt;
   out_6504624075581041956[105] = 0;
   out_6504624075581041956[106] = 0;
   out_6504624075581041956[107] = 0;
   out_6504624075581041956[108] = 0;
   out_6504624075581041956[109] = 0;
   out_6504624075581041956[110] = 0;
   out_6504624075581041956[111] = 0;
   out_6504624075581041956[112] = 0;
   out_6504624075581041956[113] = 0;
   out_6504624075581041956[114] = 1;
   out_6504624075581041956[115] = 0;
   out_6504624075581041956[116] = 0;
   out_6504624075581041956[117] = 0;
   out_6504624075581041956[118] = 0;
   out_6504624075581041956[119] = 0;
   out_6504624075581041956[120] = 0;
   out_6504624075581041956[121] = 0;
   out_6504624075581041956[122] = 0;
   out_6504624075581041956[123] = 0;
   out_6504624075581041956[124] = 0;
   out_6504624075581041956[125] = 0;
   out_6504624075581041956[126] = 0;
   out_6504624075581041956[127] = 0;
   out_6504624075581041956[128] = 0;
   out_6504624075581041956[129] = 0;
   out_6504624075581041956[130] = 0;
   out_6504624075581041956[131] = 0;
   out_6504624075581041956[132] = 0;
   out_6504624075581041956[133] = 1;
   out_6504624075581041956[134] = 0;
   out_6504624075581041956[135] = 0;
   out_6504624075581041956[136] = 0;
   out_6504624075581041956[137] = 0;
   out_6504624075581041956[138] = 0;
   out_6504624075581041956[139] = 0;
   out_6504624075581041956[140] = 0;
   out_6504624075581041956[141] = 0;
   out_6504624075581041956[142] = 0;
   out_6504624075581041956[143] = 0;
   out_6504624075581041956[144] = 0;
   out_6504624075581041956[145] = 0;
   out_6504624075581041956[146] = 0;
   out_6504624075581041956[147] = 0;
   out_6504624075581041956[148] = 0;
   out_6504624075581041956[149] = 0;
   out_6504624075581041956[150] = 0;
   out_6504624075581041956[151] = 0;
   out_6504624075581041956[152] = 1;
   out_6504624075581041956[153] = 0;
   out_6504624075581041956[154] = 0;
   out_6504624075581041956[155] = 0;
   out_6504624075581041956[156] = 0;
   out_6504624075581041956[157] = 0;
   out_6504624075581041956[158] = 0;
   out_6504624075581041956[159] = 0;
   out_6504624075581041956[160] = 0;
   out_6504624075581041956[161] = 0;
   out_6504624075581041956[162] = 0;
   out_6504624075581041956[163] = 0;
   out_6504624075581041956[164] = 0;
   out_6504624075581041956[165] = 0;
   out_6504624075581041956[166] = 0;
   out_6504624075581041956[167] = 0;
   out_6504624075581041956[168] = 0;
   out_6504624075581041956[169] = 0;
   out_6504624075581041956[170] = 0;
   out_6504624075581041956[171] = 1;
   out_6504624075581041956[172] = 0;
   out_6504624075581041956[173] = 0;
   out_6504624075581041956[174] = 0;
   out_6504624075581041956[175] = 0;
   out_6504624075581041956[176] = 0;
   out_6504624075581041956[177] = 0;
   out_6504624075581041956[178] = 0;
   out_6504624075581041956[179] = 0;
   out_6504624075581041956[180] = 0;
   out_6504624075581041956[181] = 0;
   out_6504624075581041956[182] = 0;
   out_6504624075581041956[183] = 0;
   out_6504624075581041956[184] = 0;
   out_6504624075581041956[185] = 0;
   out_6504624075581041956[186] = 0;
   out_6504624075581041956[187] = 0;
   out_6504624075581041956[188] = 0;
   out_6504624075581041956[189] = 0;
   out_6504624075581041956[190] = 1;
   out_6504624075581041956[191] = 0;
   out_6504624075581041956[192] = 0;
   out_6504624075581041956[193] = 0;
   out_6504624075581041956[194] = 0;
   out_6504624075581041956[195] = 0;
   out_6504624075581041956[196] = 0;
   out_6504624075581041956[197] = 0;
   out_6504624075581041956[198] = 0;
   out_6504624075581041956[199] = 0;
   out_6504624075581041956[200] = 0;
   out_6504624075581041956[201] = 0;
   out_6504624075581041956[202] = 0;
   out_6504624075581041956[203] = 0;
   out_6504624075581041956[204] = 0;
   out_6504624075581041956[205] = 0;
   out_6504624075581041956[206] = 0;
   out_6504624075581041956[207] = 0;
   out_6504624075581041956[208] = 0;
   out_6504624075581041956[209] = 1;
   out_6504624075581041956[210] = 0;
   out_6504624075581041956[211] = 0;
   out_6504624075581041956[212] = 0;
   out_6504624075581041956[213] = 0;
   out_6504624075581041956[214] = 0;
   out_6504624075581041956[215] = 0;
   out_6504624075581041956[216] = 0;
   out_6504624075581041956[217] = 0;
   out_6504624075581041956[218] = 0;
   out_6504624075581041956[219] = 0;
   out_6504624075581041956[220] = 0;
   out_6504624075581041956[221] = 0;
   out_6504624075581041956[222] = 0;
   out_6504624075581041956[223] = 0;
   out_6504624075581041956[224] = 0;
   out_6504624075581041956[225] = 0;
   out_6504624075581041956[226] = 0;
   out_6504624075581041956[227] = 0;
   out_6504624075581041956[228] = 1;
   out_6504624075581041956[229] = 0;
   out_6504624075581041956[230] = 0;
   out_6504624075581041956[231] = 0;
   out_6504624075581041956[232] = 0;
   out_6504624075581041956[233] = 0;
   out_6504624075581041956[234] = 0;
   out_6504624075581041956[235] = 0;
   out_6504624075581041956[236] = 0;
   out_6504624075581041956[237] = 0;
   out_6504624075581041956[238] = 0;
   out_6504624075581041956[239] = 0;
   out_6504624075581041956[240] = 0;
   out_6504624075581041956[241] = 0;
   out_6504624075581041956[242] = 0;
   out_6504624075581041956[243] = 0;
   out_6504624075581041956[244] = 0;
   out_6504624075581041956[245] = 0;
   out_6504624075581041956[246] = 0;
   out_6504624075581041956[247] = 1;
   out_6504624075581041956[248] = 0;
   out_6504624075581041956[249] = 0;
   out_6504624075581041956[250] = 0;
   out_6504624075581041956[251] = 0;
   out_6504624075581041956[252] = 0;
   out_6504624075581041956[253] = 0;
   out_6504624075581041956[254] = 0;
   out_6504624075581041956[255] = 0;
   out_6504624075581041956[256] = 0;
   out_6504624075581041956[257] = 0;
   out_6504624075581041956[258] = 0;
   out_6504624075581041956[259] = 0;
   out_6504624075581041956[260] = 0;
   out_6504624075581041956[261] = 0;
   out_6504624075581041956[262] = 0;
   out_6504624075581041956[263] = 0;
   out_6504624075581041956[264] = 0;
   out_6504624075581041956[265] = 0;
   out_6504624075581041956[266] = 1;
   out_6504624075581041956[267] = 0;
   out_6504624075581041956[268] = 0;
   out_6504624075581041956[269] = 0;
   out_6504624075581041956[270] = 0;
   out_6504624075581041956[271] = 0;
   out_6504624075581041956[272] = 0;
   out_6504624075581041956[273] = 0;
   out_6504624075581041956[274] = 0;
   out_6504624075581041956[275] = 0;
   out_6504624075581041956[276] = 0;
   out_6504624075581041956[277] = 0;
   out_6504624075581041956[278] = 0;
   out_6504624075581041956[279] = 0;
   out_6504624075581041956[280] = 0;
   out_6504624075581041956[281] = 0;
   out_6504624075581041956[282] = 0;
   out_6504624075581041956[283] = 0;
   out_6504624075581041956[284] = 0;
   out_6504624075581041956[285] = 1;
   out_6504624075581041956[286] = 0;
   out_6504624075581041956[287] = 0;
   out_6504624075581041956[288] = 0;
   out_6504624075581041956[289] = 0;
   out_6504624075581041956[290] = 0;
   out_6504624075581041956[291] = 0;
   out_6504624075581041956[292] = 0;
   out_6504624075581041956[293] = 0;
   out_6504624075581041956[294] = 0;
   out_6504624075581041956[295] = 0;
   out_6504624075581041956[296] = 0;
   out_6504624075581041956[297] = 0;
   out_6504624075581041956[298] = 0;
   out_6504624075581041956[299] = 0;
   out_6504624075581041956[300] = 0;
   out_6504624075581041956[301] = 0;
   out_6504624075581041956[302] = 0;
   out_6504624075581041956[303] = 0;
   out_6504624075581041956[304] = 1;
   out_6504624075581041956[305] = 0;
   out_6504624075581041956[306] = 0;
   out_6504624075581041956[307] = 0;
   out_6504624075581041956[308] = 0;
   out_6504624075581041956[309] = 0;
   out_6504624075581041956[310] = 0;
   out_6504624075581041956[311] = 0;
   out_6504624075581041956[312] = 0;
   out_6504624075581041956[313] = 0;
   out_6504624075581041956[314] = 0;
   out_6504624075581041956[315] = 0;
   out_6504624075581041956[316] = 0;
   out_6504624075581041956[317] = 0;
   out_6504624075581041956[318] = 0;
   out_6504624075581041956[319] = 0;
   out_6504624075581041956[320] = 0;
   out_6504624075581041956[321] = 0;
   out_6504624075581041956[322] = 0;
   out_6504624075581041956[323] = 1;
}
void h_4(double *state, double *unused, double *out_7873091896089738414) {
   out_7873091896089738414[0] = state[6] + state[9];
   out_7873091896089738414[1] = state[7] + state[10];
   out_7873091896089738414[2] = state[8] + state[11];
}
void H_4(double *state, double *unused, double *out_8703729345658275828) {
   out_8703729345658275828[0] = 0;
   out_8703729345658275828[1] = 0;
   out_8703729345658275828[2] = 0;
   out_8703729345658275828[3] = 0;
   out_8703729345658275828[4] = 0;
   out_8703729345658275828[5] = 0;
   out_8703729345658275828[6] = 1;
   out_8703729345658275828[7] = 0;
   out_8703729345658275828[8] = 0;
   out_8703729345658275828[9] = 1;
   out_8703729345658275828[10] = 0;
   out_8703729345658275828[11] = 0;
   out_8703729345658275828[12] = 0;
   out_8703729345658275828[13] = 0;
   out_8703729345658275828[14] = 0;
   out_8703729345658275828[15] = 0;
   out_8703729345658275828[16] = 0;
   out_8703729345658275828[17] = 0;
   out_8703729345658275828[18] = 0;
   out_8703729345658275828[19] = 0;
   out_8703729345658275828[20] = 0;
   out_8703729345658275828[21] = 0;
   out_8703729345658275828[22] = 0;
   out_8703729345658275828[23] = 0;
   out_8703729345658275828[24] = 0;
   out_8703729345658275828[25] = 1;
   out_8703729345658275828[26] = 0;
   out_8703729345658275828[27] = 0;
   out_8703729345658275828[28] = 1;
   out_8703729345658275828[29] = 0;
   out_8703729345658275828[30] = 0;
   out_8703729345658275828[31] = 0;
   out_8703729345658275828[32] = 0;
   out_8703729345658275828[33] = 0;
   out_8703729345658275828[34] = 0;
   out_8703729345658275828[35] = 0;
   out_8703729345658275828[36] = 0;
   out_8703729345658275828[37] = 0;
   out_8703729345658275828[38] = 0;
   out_8703729345658275828[39] = 0;
   out_8703729345658275828[40] = 0;
   out_8703729345658275828[41] = 0;
   out_8703729345658275828[42] = 0;
   out_8703729345658275828[43] = 0;
   out_8703729345658275828[44] = 1;
   out_8703729345658275828[45] = 0;
   out_8703729345658275828[46] = 0;
   out_8703729345658275828[47] = 1;
   out_8703729345658275828[48] = 0;
   out_8703729345658275828[49] = 0;
   out_8703729345658275828[50] = 0;
   out_8703729345658275828[51] = 0;
   out_8703729345658275828[52] = 0;
   out_8703729345658275828[53] = 0;
}
void h_10(double *state, double *unused, double *out_8481552048721867035) {
   out_8481552048721867035[0] = 9.8100000000000005*sin(state[1]) - state[4]*state[8] + state[5]*state[7] + state[12] + state[15];
   out_8481552048721867035[1] = -9.8100000000000005*sin(state[0])*cos(state[1]) + state[3]*state[8] - state[5]*state[6] + state[13] + state[16];
   out_8481552048721867035[2] = -9.8100000000000005*cos(state[0])*cos(state[1]) - state[3]*state[7] + state[4]*state[6] + state[14] + state[17];
}
void H_10(double *state, double *unused, double *out_9046581129967679223) {
   out_9046581129967679223[0] = 0;
   out_9046581129967679223[1] = 9.8100000000000005*cos(state[1]);
   out_9046581129967679223[2] = 0;
   out_9046581129967679223[3] = 0;
   out_9046581129967679223[4] = -state[8];
   out_9046581129967679223[5] = state[7];
   out_9046581129967679223[6] = 0;
   out_9046581129967679223[7] = state[5];
   out_9046581129967679223[8] = -state[4];
   out_9046581129967679223[9] = 0;
   out_9046581129967679223[10] = 0;
   out_9046581129967679223[11] = 0;
   out_9046581129967679223[12] = 1;
   out_9046581129967679223[13] = 0;
   out_9046581129967679223[14] = 0;
   out_9046581129967679223[15] = 1;
   out_9046581129967679223[16] = 0;
   out_9046581129967679223[17] = 0;
   out_9046581129967679223[18] = -9.8100000000000005*cos(state[0])*cos(state[1]);
   out_9046581129967679223[19] = 9.8100000000000005*sin(state[0])*sin(state[1]);
   out_9046581129967679223[20] = 0;
   out_9046581129967679223[21] = state[8];
   out_9046581129967679223[22] = 0;
   out_9046581129967679223[23] = -state[6];
   out_9046581129967679223[24] = -state[5];
   out_9046581129967679223[25] = 0;
   out_9046581129967679223[26] = state[3];
   out_9046581129967679223[27] = 0;
   out_9046581129967679223[28] = 0;
   out_9046581129967679223[29] = 0;
   out_9046581129967679223[30] = 0;
   out_9046581129967679223[31] = 1;
   out_9046581129967679223[32] = 0;
   out_9046581129967679223[33] = 0;
   out_9046581129967679223[34] = 1;
   out_9046581129967679223[35] = 0;
   out_9046581129967679223[36] = 9.8100000000000005*sin(state[0])*cos(state[1]);
   out_9046581129967679223[37] = 9.8100000000000005*sin(state[1])*cos(state[0]);
   out_9046581129967679223[38] = 0;
   out_9046581129967679223[39] = -state[7];
   out_9046581129967679223[40] = state[6];
   out_9046581129967679223[41] = 0;
   out_9046581129967679223[42] = state[4];
   out_9046581129967679223[43] = -state[3];
   out_9046581129967679223[44] = 0;
   out_9046581129967679223[45] = 0;
   out_9046581129967679223[46] = 0;
   out_9046581129967679223[47] = 0;
   out_9046581129967679223[48] = 0;
   out_9046581129967679223[49] = 0;
   out_9046581129967679223[50] = 1;
   out_9046581129967679223[51] = 0;
   out_9046581129967679223[52] = 0;
   out_9046581129967679223[53] = 1;
}
void h_13(double *state, double *unused, double *out_2079745877024079652) {
   out_2079745877024079652[0] = state[3];
   out_2079745877024079652[1] = state[4];
   out_2079745877024079652[2] = state[5];
}
void H_13(double *state, double *unused, double *out_6530740902718942987) {
   out_6530740902718942987[0] = 0;
   out_6530740902718942987[1] = 0;
   out_6530740902718942987[2] = 0;
   out_6530740902718942987[3] = 1;
   out_6530740902718942987[4] = 0;
   out_6530740902718942987[5] = 0;
   out_6530740902718942987[6] = 0;
   out_6530740902718942987[7] = 0;
   out_6530740902718942987[8] = 0;
   out_6530740902718942987[9] = 0;
   out_6530740902718942987[10] = 0;
   out_6530740902718942987[11] = 0;
   out_6530740902718942987[12] = 0;
   out_6530740902718942987[13] = 0;
   out_6530740902718942987[14] = 0;
   out_6530740902718942987[15] = 0;
   out_6530740902718942987[16] = 0;
   out_6530740902718942987[17] = 0;
   out_6530740902718942987[18] = 0;
   out_6530740902718942987[19] = 0;
   out_6530740902718942987[20] = 0;
   out_6530740902718942987[21] = 0;
   out_6530740902718942987[22] = 1;
   out_6530740902718942987[23] = 0;
   out_6530740902718942987[24] = 0;
   out_6530740902718942987[25] = 0;
   out_6530740902718942987[26] = 0;
   out_6530740902718942987[27] = 0;
   out_6530740902718942987[28] = 0;
   out_6530740902718942987[29] = 0;
   out_6530740902718942987[30] = 0;
   out_6530740902718942987[31] = 0;
   out_6530740902718942987[32] = 0;
   out_6530740902718942987[33] = 0;
   out_6530740902718942987[34] = 0;
   out_6530740902718942987[35] = 0;
   out_6530740902718942987[36] = 0;
   out_6530740902718942987[37] = 0;
   out_6530740902718942987[38] = 0;
   out_6530740902718942987[39] = 0;
   out_6530740902718942987[40] = 0;
   out_6530740902718942987[41] = 1;
   out_6530740902718942987[42] = 0;
   out_6530740902718942987[43] = 0;
   out_6530740902718942987[44] = 0;
   out_6530740902718942987[45] = 0;
   out_6530740902718942987[46] = 0;
   out_6530740902718942987[47] = 0;
   out_6530740902718942987[48] = 0;
   out_6530740902718942987[49] = 0;
   out_6530740902718942987[50] = 0;
   out_6530740902718942987[51] = 0;
   out_6530740902718942987[52] = 0;
   out_6530740902718942987[53] = 0;
}
void h_14(double *state, double *unused, double *out_5967769102958667382) {
   out_5967769102958667382[0] = state[6];
   out_5967769102958667382[1] = state[7];
   out_5967769102958667382[2] = state[8];
}
void H_14(double *state, double *unused, double *out_5779773871711791259) {
   out_5779773871711791259[0] = 0;
   out_5779773871711791259[1] = 0;
   out_5779773871711791259[2] = 0;
   out_5779773871711791259[3] = 0;
   out_5779773871711791259[4] = 0;
   out_5779773871711791259[5] = 0;
   out_5779773871711791259[6] = 1;
   out_5779773871711791259[7] = 0;
   out_5779773871711791259[8] = 0;
   out_5779773871711791259[9] = 0;
   out_5779773871711791259[10] = 0;
   out_5779773871711791259[11] = 0;
   out_5779773871711791259[12] = 0;
   out_5779773871711791259[13] = 0;
   out_5779773871711791259[14] = 0;
   out_5779773871711791259[15] = 0;
   out_5779773871711791259[16] = 0;
   out_5779773871711791259[17] = 0;
   out_5779773871711791259[18] = 0;
   out_5779773871711791259[19] = 0;
   out_5779773871711791259[20] = 0;
   out_5779773871711791259[21] = 0;
   out_5779773871711791259[22] = 0;
   out_5779773871711791259[23] = 0;
   out_5779773871711791259[24] = 0;
   out_5779773871711791259[25] = 1;
   out_5779773871711791259[26] = 0;
   out_5779773871711791259[27] = 0;
   out_5779773871711791259[28] = 0;
   out_5779773871711791259[29] = 0;
   out_5779773871711791259[30] = 0;
   out_5779773871711791259[31] = 0;
   out_5779773871711791259[32] = 0;
   out_5779773871711791259[33] = 0;
   out_5779773871711791259[34] = 0;
   out_5779773871711791259[35] = 0;
   out_5779773871711791259[36] = 0;
   out_5779773871711791259[37] = 0;
   out_5779773871711791259[38] = 0;
   out_5779773871711791259[39] = 0;
   out_5779773871711791259[40] = 0;
   out_5779773871711791259[41] = 0;
   out_5779773871711791259[42] = 0;
   out_5779773871711791259[43] = 0;
   out_5779773871711791259[44] = 1;
   out_5779773871711791259[45] = 0;
   out_5779773871711791259[46] = 0;
   out_5779773871711791259[47] = 0;
   out_5779773871711791259[48] = 0;
   out_5779773871711791259[49] = 0;
   out_5779773871711791259[50] = 0;
   out_5779773871711791259[51] = 0;
   out_5779773871711791259[52] = 0;
   out_5779773871711791259[53] = 0;
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
void pose_err_fun(double *nom_x, double *delta_x, double *out_8357459543021008699) {
  err_fun(nom_x, delta_x, out_8357459543021008699);
}
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_3253386232645254795) {
  inv_err_fun(nom_x, true_x, out_3253386232645254795);
}
void pose_H_mod_fun(double *state, double *out_3551210960619195093) {
  H_mod_fun(state, out_3551210960619195093);
}
void pose_f_fun(double *state, double dt, double *out_3486488515702872216) {
  f_fun(state,  dt, out_3486488515702872216);
}
void pose_F_fun(double *state, double dt, double *out_6504624075581041956) {
  F_fun(state,  dt, out_6504624075581041956);
}
void pose_h_4(double *state, double *unused, double *out_7873091896089738414) {
  h_4(state, unused, out_7873091896089738414);
}
void pose_H_4(double *state, double *unused, double *out_8703729345658275828) {
  H_4(state, unused, out_8703729345658275828);
}
void pose_h_10(double *state, double *unused, double *out_8481552048721867035) {
  h_10(state, unused, out_8481552048721867035);
}
void pose_H_10(double *state, double *unused, double *out_9046581129967679223) {
  H_10(state, unused, out_9046581129967679223);
}
void pose_h_13(double *state, double *unused, double *out_2079745877024079652) {
  h_13(state, unused, out_2079745877024079652);
}
void pose_H_13(double *state, double *unused, double *out_6530740902718942987) {
  H_13(state, unused, out_6530740902718942987);
}
void pose_h_14(double *state, double *unused, double *out_5967769102958667382) {
  h_14(state, unused, out_5967769102958667382);
}
void pose_H_14(double *state, double *unused, double *out_5779773871711791259) {
  H_14(state, unused, out_5779773871711791259);
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
