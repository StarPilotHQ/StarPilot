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
void err_fun(double *nom_x, double *delta_x, double *out_4349191364545344677) {
   out_4349191364545344677[0] = delta_x[0] + nom_x[0];
   out_4349191364545344677[1] = delta_x[1] + nom_x[1];
   out_4349191364545344677[2] = delta_x[2] + nom_x[2];
   out_4349191364545344677[3] = delta_x[3] + nom_x[3];
   out_4349191364545344677[4] = delta_x[4] + nom_x[4];
   out_4349191364545344677[5] = delta_x[5] + nom_x[5];
   out_4349191364545344677[6] = delta_x[6] + nom_x[6];
   out_4349191364545344677[7] = delta_x[7] + nom_x[7];
   out_4349191364545344677[8] = delta_x[8] + nom_x[8];
   out_4349191364545344677[9] = delta_x[9] + nom_x[9];
   out_4349191364545344677[10] = delta_x[10] + nom_x[10];
   out_4349191364545344677[11] = delta_x[11] + nom_x[11];
   out_4349191364545344677[12] = delta_x[12] + nom_x[12];
   out_4349191364545344677[13] = delta_x[13] + nom_x[13];
   out_4349191364545344677[14] = delta_x[14] + nom_x[14];
   out_4349191364545344677[15] = delta_x[15] + nom_x[15];
   out_4349191364545344677[16] = delta_x[16] + nom_x[16];
   out_4349191364545344677[17] = delta_x[17] + nom_x[17];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_6050387772030197335) {
   out_6050387772030197335[0] = -nom_x[0] + true_x[0];
   out_6050387772030197335[1] = -nom_x[1] + true_x[1];
   out_6050387772030197335[2] = -nom_x[2] + true_x[2];
   out_6050387772030197335[3] = -nom_x[3] + true_x[3];
   out_6050387772030197335[4] = -nom_x[4] + true_x[4];
   out_6050387772030197335[5] = -nom_x[5] + true_x[5];
   out_6050387772030197335[6] = -nom_x[6] + true_x[6];
   out_6050387772030197335[7] = -nom_x[7] + true_x[7];
   out_6050387772030197335[8] = -nom_x[8] + true_x[8];
   out_6050387772030197335[9] = -nom_x[9] + true_x[9];
   out_6050387772030197335[10] = -nom_x[10] + true_x[10];
   out_6050387772030197335[11] = -nom_x[11] + true_x[11];
   out_6050387772030197335[12] = -nom_x[12] + true_x[12];
   out_6050387772030197335[13] = -nom_x[13] + true_x[13];
   out_6050387772030197335[14] = -nom_x[14] + true_x[14];
   out_6050387772030197335[15] = -nom_x[15] + true_x[15];
   out_6050387772030197335[16] = -nom_x[16] + true_x[16];
   out_6050387772030197335[17] = -nom_x[17] + true_x[17];
}
void H_mod_fun(double *state, double *out_7888777177672614690) {
   out_7888777177672614690[0] = 1.0;
   out_7888777177672614690[1] = 0.0;
   out_7888777177672614690[2] = 0.0;
   out_7888777177672614690[3] = 0.0;
   out_7888777177672614690[4] = 0.0;
   out_7888777177672614690[5] = 0.0;
   out_7888777177672614690[6] = 0.0;
   out_7888777177672614690[7] = 0.0;
   out_7888777177672614690[8] = 0.0;
   out_7888777177672614690[9] = 0.0;
   out_7888777177672614690[10] = 0.0;
   out_7888777177672614690[11] = 0.0;
   out_7888777177672614690[12] = 0.0;
   out_7888777177672614690[13] = 0.0;
   out_7888777177672614690[14] = 0.0;
   out_7888777177672614690[15] = 0.0;
   out_7888777177672614690[16] = 0.0;
   out_7888777177672614690[17] = 0.0;
   out_7888777177672614690[18] = 0.0;
   out_7888777177672614690[19] = 1.0;
   out_7888777177672614690[20] = 0.0;
   out_7888777177672614690[21] = 0.0;
   out_7888777177672614690[22] = 0.0;
   out_7888777177672614690[23] = 0.0;
   out_7888777177672614690[24] = 0.0;
   out_7888777177672614690[25] = 0.0;
   out_7888777177672614690[26] = 0.0;
   out_7888777177672614690[27] = 0.0;
   out_7888777177672614690[28] = 0.0;
   out_7888777177672614690[29] = 0.0;
   out_7888777177672614690[30] = 0.0;
   out_7888777177672614690[31] = 0.0;
   out_7888777177672614690[32] = 0.0;
   out_7888777177672614690[33] = 0.0;
   out_7888777177672614690[34] = 0.0;
   out_7888777177672614690[35] = 0.0;
   out_7888777177672614690[36] = 0.0;
   out_7888777177672614690[37] = 0.0;
   out_7888777177672614690[38] = 1.0;
   out_7888777177672614690[39] = 0.0;
   out_7888777177672614690[40] = 0.0;
   out_7888777177672614690[41] = 0.0;
   out_7888777177672614690[42] = 0.0;
   out_7888777177672614690[43] = 0.0;
   out_7888777177672614690[44] = 0.0;
   out_7888777177672614690[45] = 0.0;
   out_7888777177672614690[46] = 0.0;
   out_7888777177672614690[47] = 0.0;
   out_7888777177672614690[48] = 0.0;
   out_7888777177672614690[49] = 0.0;
   out_7888777177672614690[50] = 0.0;
   out_7888777177672614690[51] = 0.0;
   out_7888777177672614690[52] = 0.0;
   out_7888777177672614690[53] = 0.0;
   out_7888777177672614690[54] = 0.0;
   out_7888777177672614690[55] = 0.0;
   out_7888777177672614690[56] = 0.0;
   out_7888777177672614690[57] = 1.0;
   out_7888777177672614690[58] = 0.0;
   out_7888777177672614690[59] = 0.0;
   out_7888777177672614690[60] = 0.0;
   out_7888777177672614690[61] = 0.0;
   out_7888777177672614690[62] = 0.0;
   out_7888777177672614690[63] = 0.0;
   out_7888777177672614690[64] = 0.0;
   out_7888777177672614690[65] = 0.0;
   out_7888777177672614690[66] = 0.0;
   out_7888777177672614690[67] = 0.0;
   out_7888777177672614690[68] = 0.0;
   out_7888777177672614690[69] = 0.0;
   out_7888777177672614690[70] = 0.0;
   out_7888777177672614690[71] = 0.0;
   out_7888777177672614690[72] = 0.0;
   out_7888777177672614690[73] = 0.0;
   out_7888777177672614690[74] = 0.0;
   out_7888777177672614690[75] = 0.0;
   out_7888777177672614690[76] = 1.0;
   out_7888777177672614690[77] = 0.0;
   out_7888777177672614690[78] = 0.0;
   out_7888777177672614690[79] = 0.0;
   out_7888777177672614690[80] = 0.0;
   out_7888777177672614690[81] = 0.0;
   out_7888777177672614690[82] = 0.0;
   out_7888777177672614690[83] = 0.0;
   out_7888777177672614690[84] = 0.0;
   out_7888777177672614690[85] = 0.0;
   out_7888777177672614690[86] = 0.0;
   out_7888777177672614690[87] = 0.0;
   out_7888777177672614690[88] = 0.0;
   out_7888777177672614690[89] = 0.0;
   out_7888777177672614690[90] = 0.0;
   out_7888777177672614690[91] = 0.0;
   out_7888777177672614690[92] = 0.0;
   out_7888777177672614690[93] = 0.0;
   out_7888777177672614690[94] = 0.0;
   out_7888777177672614690[95] = 1.0;
   out_7888777177672614690[96] = 0.0;
   out_7888777177672614690[97] = 0.0;
   out_7888777177672614690[98] = 0.0;
   out_7888777177672614690[99] = 0.0;
   out_7888777177672614690[100] = 0.0;
   out_7888777177672614690[101] = 0.0;
   out_7888777177672614690[102] = 0.0;
   out_7888777177672614690[103] = 0.0;
   out_7888777177672614690[104] = 0.0;
   out_7888777177672614690[105] = 0.0;
   out_7888777177672614690[106] = 0.0;
   out_7888777177672614690[107] = 0.0;
   out_7888777177672614690[108] = 0.0;
   out_7888777177672614690[109] = 0.0;
   out_7888777177672614690[110] = 0.0;
   out_7888777177672614690[111] = 0.0;
   out_7888777177672614690[112] = 0.0;
   out_7888777177672614690[113] = 0.0;
   out_7888777177672614690[114] = 1.0;
   out_7888777177672614690[115] = 0.0;
   out_7888777177672614690[116] = 0.0;
   out_7888777177672614690[117] = 0.0;
   out_7888777177672614690[118] = 0.0;
   out_7888777177672614690[119] = 0.0;
   out_7888777177672614690[120] = 0.0;
   out_7888777177672614690[121] = 0.0;
   out_7888777177672614690[122] = 0.0;
   out_7888777177672614690[123] = 0.0;
   out_7888777177672614690[124] = 0.0;
   out_7888777177672614690[125] = 0.0;
   out_7888777177672614690[126] = 0.0;
   out_7888777177672614690[127] = 0.0;
   out_7888777177672614690[128] = 0.0;
   out_7888777177672614690[129] = 0.0;
   out_7888777177672614690[130] = 0.0;
   out_7888777177672614690[131] = 0.0;
   out_7888777177672614690[132] = 0.0;
   out_7888777177672614690[133] = 1.0;
   out_7888777177672614690[134] = 0.0;
   out_7888777177672614690[135] = 0.0;
   out_7888777177672614690[136] = 0.0;
   out_7888777177672614690[137] = 0.0;
   out_7888777177672614690[138] = 0.0;
   out_7888777177672614690[139] = 0.0;
   out_7888777177672614690[140] = 0.0;
   out_7888777177672614690[141] = 0.0;
   out_7888777177672614690[142] = 0.0;
   out_7888777177672614690[143] = 0.0;
   out_7888777177672614690[144] = 0.0;
   out_7888777177672614690[145] = 0.0;
   out_7888777177672614690[146] = 0.0;
   out_7888777177672614690[147] = 0.0;
   out_7888777177672614690[148] = 0.0;
   out_7888777177672614690[149] = 0.0;
   out_7888777177672614690[150] = 0.0;
   out_7888777177672614690[151] = 0.0;
   out_7888777177672614690[152] = 1.0;
   out_7888777177672614690[153] = 0.0;
   out_7888777177672614690[154] = 0.0;
   out_7888777177672614690[155] = 0.0;
   out_7888777177672614690[156] = 0.0;
   out_7888777177672614690[157] = 0.0;
   out_7888777177672614690[158] = 0.0;
   out_7888777177672614690[159] = 0.0;
   out_7888777177672614690[160] = 0.0;
   out_7888777177672614690[161] = 0.0;
   out_7888777177672614690[162] = 0.0;
   out_7888777177672614690[163] = 0.0;
   out_7888777177672614690[164] = 0.0;
   out_7888777177672614690[165] = 0.0;
   out_7888777177672614690[166] = 0.0;
   out_7888777177672614690[167] = 0.0;
   out_7888777177672614690[168] = 0.0;
   out_7888777177672614690[169] = 0.0;
   out_7888777177672614690[170] = 0.0;
   out_7888777177672614690[171] = 1.0;
   out_7888777177672614690[172] = 0.0;
   out_7888777177672614690[173] = 0.0;
   out_7888777177672614690[174] = 0.0;
   out_7888777177672614690[175] = 0.0;
   out_7888777177672614690[176] = 0.0;
   out_7888777177672614690[177] = 0.0;
   out_7888777177672614690[178] = 0.0;
   out_7888777177672614690[179] = 0.0;
   out_7888777177672614690[180] = 0.0;
   out_7888777177672614690[181] = 0.0;
   out_7888777177672614690[182] = 0.0;
   out_7888777177672614690[183] = 0.0;
   out_7888777177672614690[184] = 0.0;
   out_7888777177672614690[185] = 0.0;
   out_7888777177672614690[186] = 0.0;
   out_7888777177672614690[187] = 0.0;
   out_7888777177672614690[188] = 0.0;
   out_7888777177672614690[189] = 0.0;
   out_7888777177672614690[190] = 1.0;
   out_7888777177672614690[191] = 0.0;
   out_7888777177672614690[192] = 0.0;
   out_7888777177672614690[193] = 0.0;
   out_7888777177672614690[194] = 0.0;
   out_7888777177672614690[195] = 0.0;
   out_7888777177672614690[196] = 0.0;
   out_7888777177672614690[197] = 0.0;
   out_7888777177672614690[198] = 0.0;
   out_7888777177672614690[199] = 0.0;
   out_7888777177672614690[200] = 0.0;
   out_7888777177672614690[201] = 0.0;
   out_7888777177672614690[202] = 0.0;
   out_7888777177672614690[203] = 0.0;
   out_7888777177672614690[204] = 0.0;
   out_7888777177672614690[205] = 0.0;
   out_7888777177672614690[206] = 0.0;
   out_7888777177672614690[207] = 0.0;
   out_7888777177672614690[208] = 0.0;
   out_7888777177672614690[209] = 1.0;
   out_7888777177672614690[210] = 0.0;
   out_7888777177672614690[211] = 0.0;
   out_7888777177672614690[212] = 0.0;
   out_7888777177672614690[213] = 0.0;
   out_7888777177672614690[214] = 0.0;
   out_7888777177672614690[215] = 0.0;
   out_7888777177672614690[216] = 0.0;
   out_7888777177672614690[217] = 0.0;
   out_7888777177672614690[218] = 0.0;
   out_7888777177672614690[219] = 0.0;
   out_7888777177672614690[220] = 0.0;
   out_7888777177672614690[221] = 0.0;
   out_7888777177672614690[222] = 0.0;
   out_7888777177672614690[223] = 0.0;
   out_7888777177672614690[224] = 0.0;
   out_7888777177672614690[225] = 0.0;
   out_7888777177672614690[226] = 0.0;
   out_7888777177672614690[227] = 0.0;
   out_7888777177672614690[228] = 1.0;
   out_7888777177672614690[229] = 0.0;
   out_7888777177672614690[230] = 0.0;
   out_7888777177672614690[231] = 0.0;
   out_7888777177672614690[232] = 0.0;
   out_7888777177672614690[233] = 0.0;
   out_7888777177672614690[234] = 0.0;
   out_7888777177672614690[235] = 0.0;
   out_7888777177672614690[236] = 0.0;
   out_7888777177672614690[237] = 0.0;
   out_7888777177672614690[238] = 0.0;
   out_7888777177672614690[239] = 0.0;
   out_7888777177672614690[240] = 0.0;
   out_7888777177672614690[241] = 0.0;
   out_7888777177672614690[242] = 0.0;
   out_7888777177672614690[243] = 0.0;
   out_7888777177672614690[244] = 0.0;
   out_7888777177672614690[245] = 0.0;
   out_7888777177672614690[246] = 0.0;
   out_7888777177672614690[247] = 1.0;
   out_7888777177672614690[248] = 0.0;
   out_7888777177672614690[249] = 0.0;
   out_7888777177672614690[250] = 0.0;
   out_7888777177672614690[251] = 0.0;
   out_7888777177672614690[252] = 0.0;
   out_7888777177672614690[253] = 0.0;
   out_7888777177672614690[254] = 0.0;
   out_7888777177672614690[255] = 0.0;
   out_7888777177672614690[256] = 0.0;
   out_7888777177672614690[257] = 0.0;
   out_7888777177672614690[258] = 0.0;
   out_7888777177672614690[259] = 0.0;
   out_7888777177672614690[260] = 0.0;
   out_7888777177672614690[261] = 0.0;
   out_7888777177672614690[262] = 0.0;
   out_7888777177672614690[263] = 0.0;
   out_7888777177672614690[264] = 0.0;
   out_7888777177672614690[265] = 0.0;
   out_7888777177672614690[266] = 1.0;
   out_7888777177672614690[267] = 0.0;
   out_7888777177672614690[268] = 0.0;
   out_7888777177672614690[269] = 0.0;
   out_7888777177672614690[270] = 0.0;
   out_7888777177672614690[271] = 0.0;
   out_7888777177672614690[272] = 0.0;
   out_7888777177672614690[273] = 0.0;
   out_7888777177672614690[274] = 0.0;
   out_7888777177672614690[275] = 0.0;
   out_7888777177672614690[276] = 0.0;
   out_7888777177672614690[277] = 0.0;
   out_7888777177672614690[278] = 0.0;
   out_7888777177672614690[279] = 0.0;
   out_7888777177672614690[280] = 0.0;
   out_7888777177672614690[281] = 0.0;
   out_7888777177672614690[282] = 0.0;
   out_7888777177672614690[283] = 0.0;
   out_7888777177672614690[284] = 0.0;
   out_7888777177672614690[285] = 1.0;
   out_7888777177672614690[286] = 0.0;
   out_7888777177672614690[287] = 0.0;
   out_7888777177672614690[288] = 0.0;
   out_7888777177672614690[289] = 0.0;
   out_7888777177672614690[290] = 0.0;
   out_7888777177672614690[291] = 0.0;
   out_7888777177672614690[292] = 0.0;
   out_7888777177672614690[293] = 0.0;
   out_7888777177672614690[294] = 0.0;
   out_7888777177672614690[295] = 0.0;
   out_7888777177672614690[296] = 0.0;
   out_7888777177672614690[297] = 0.0;
   out_7888777177672614690[298] = 0.0;
   out_7888777177672614690[299] = 0.0;
   out_7888777177672614690[300] = 0.0;
   out_7888777177672614690[301] = 0.0;
   out_7888777177672614690[302] = 0.0;
   out_7888777177672614690[303] = 0.0;
   out_7888777177672614690[304] = 1.0;
   out_7888777177672614690[305] = 0.0;
   out_7888777177672614690[306] = 0.0;
   out_7888777177672614690[307] = 0.0;
   out_7888777177672614690[308] = 0.0;
   out_7888777177672614690[309] = 0.0;
   out_7888777177672614690[310] = 0.0;
   out_7888777177672614690[311] = 0.0;
   out_7888777177672614690[312] = 0.0;
   out_7888777177672614690[313] = 0.0;
   out_7888777177672614690[314] = 0.0;
   out_7888777177672614690[315] = 0.0;
   out_7888777177672614690[316] = 0.0;
   out_7888777177672614690[317] = 0.0;
   out_7888777177672614690[318] = 0.0;
   out_7888777177672614690[319] = 0.0;
   out_7888777177672614690[320] = 0.0;
   out_7888777177672614690[321] = 0.0;
   out_7888777177672614690[322] = 0.0;
   out_7888777177672614690[323] = 1.0;
}
void f_fun(double *state, double dt, double *out_2236332295548027163) {
   out_2236332295548027163[0] = atan2((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), -(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]));
   out_2236332295548027163[1] = asin(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]));
   out_2236332295548027163[2] = atan2(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), -(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]));
   out_2236332295548027163[3] = dt*state[12] + state[3];
   out_2236332295548027163[4] = dt*state[13] + state[4];
   out_2236332295548027163[5] = dt*state[14] + state[5];
   out_2236332295548027163[6] = state[6];
   out_2236332295548027163[7] = state[7];
   out_2236332295548027163[8] = state[8];
   out_2236332295548027163[9] = state[9];
   out_2236332295548027163[10] = state[10];
   out_2236332295548027163[11] = state[11];
   out_2236332295548027163[12] = state[12];
   out_2236332295548027163[13] = state[13];
   out_2236332295548027163[14] = state[14];
   out_2236332295548027163[15] = state[15];
   out_2236332295548027163[16] = state[16];
   out_2236332295548027163[17] = state[17];
}
void F_fun(double *state, double dt, double *out_8464137914874413999) {
   out_8464137914874413999[0] = ((-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*cos(state[0])*cos(state[1]) - sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*cos(state[0])*cos(state[1]) - sin(dt*state[6])*sin(state[0])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_8464137914874413999[1] = ((-sin(dt*state[6])*sin(dt*state[8]) - sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*cos(state[1]) - (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*sin(state[1]) - sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(state[0]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*sin(state[1]) + (-sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) + sin(dt*state[8])*cos(dt*state[6]))*cos(state[1]) - sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(state[0]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_8464137914874413999[2] = 0;
   out_8464137914874413999[3] = 0;
   out_8464137914874413999[4] = 0;
   out_8464137914874413999[5] = 0;
   out_8464137914874413999[6] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(dt*cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) - dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_8464137914874413999[7] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*sin(dt*state[7])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[6])*sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) - dt*sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[7])*cos(dt*state[6])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[8])*sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]) - dt*sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_8464137914874413999[8] = ((dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((dt*sin(dt*state[6])*sin(dt*state[8]) + dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_8464137914874413999[9] = 0;
   out_8464137914874413999[10] = 0;
   out_8464137914874413999[11] = 0;
   out_8464137914874413999[12] = 0;
   out_8464137914874413999[13] = 0;
   out_8464137914874413999[14] = 0;
   out_8464137914874413999[15] = 0;
   out_8464137914874413999[16] = 0;
   out_8464137914874413999[17] = 0;
   out_8464137914874413999[18] = (-sin(dt*state[7])*sin(state[0])*cos(state[1]) - sin(dt*state[8])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_8464137914874413999[19] = (-sin(dt*state[7])*sin(state[1])*cos(state[0]) + sin(dt*state[8])*sin(state[0])*sin(state[1])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_8464137914874413999[20] = 0;
   out_8464137914874413999[21] = 0;
   out_8464137914874413999[22] = 0;
   out_8464137914874413999[23] = 0;
   out_8464137914874413999[24] = 0;
   out_8464137914874413999[25] = (dt*sin(dt*state[7])*sin(dt*state[8])*sin(state[0])*cos(state[1]) - dt*sin(dt*state[7])*sin(state[1])*cos(dt*state[8]) + dt*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_8464137914874413999[26] = (-dt*sin(dt*state[8])*sin(state[1])*cos(dt*state[7]) - dt*sin(state[0])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_8464137914874413999[27] = 0;
   out_8464137914874413999[28] = 0;
   out_8464137914874413999[29] = 0;
   out_8464137914874413999[30] = 0;
   out_8464137914874413999[31] = 0;
   out_8464137914874413999[32] = 0;
   out_8464137914874413999[33] = 0;
   out_8464137914874413999[34] = 0;
   out_8464137914874413999[35] = 0;
   out_8464137914874413999[36] = ((sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_8464137914874413999[37] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-sin(dt*state[7])*sin(state[2])*cos(state[0])*cos(state[1]) + sin(dt*state[8])*sin(state[0])*sin(state[2])*cos(dt*state[7])*cos(state[1]) - sin(state[1])*sin(state[2])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(-sin(dt*state[7])*cos(state[0])*cos(state[1])*cos(state[2]) + sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1])*cos(state[2]) - sin(state[1])*cos(dt*state[7])*cos(dt*state[8])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_8464137914874413999[38] = ((-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (-sin(state[0])*sin(state[1])*sin(state[2]) - cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_8464137914874413999[39] = 0;
   out_8464137914874413999[40] = 0;
   out_8464137914874413999[41] = 0;
   out_8464137914874413999[42] = 0;
   out_8464137914874413999[43] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(dt*(sin(state[0])*cos(state[2]) - sin(state[1])*sin(state[2])*cos(state[0]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*sin(state[2])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(dt*(-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_8464137914874413999[44] = (dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*sin(state[2])*cos(dt*state[7])*cos(state[1]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + (dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[7])*cos(state[1])*cos(state[2]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_8464137914874413999[45] = 0;
   out_8464137914874413999[46] = 0;
   out_8464137914874413999[47] = 0;
   out_8464137914874413999[48] = 0;
   out_8464137914874413999[49] = 0;
   out_8464137914874413999[50] = 0;
   out_8464137914874413999[51] = 0;
   out_8464137914874413999[52] = 0;
   out_8464137914874413999[53] = 0;
   out_8464137914874413999[54] = 0;
   out_8464137914874413999[55] = 0;
   out_8464137914874413999[56] = 0;
   out_8464137914874413999[57] = 1;
   out_8464137914874413999[58] = 0;
   out_8464137914874413999[59] = 0;
   out_8464137914874413999[60] = 0;
   out_8464137914874413999[61] = 0;
   out_8464137914874413999[62] = 0;
   out_8464137914874413999[63] = 0;
   out_8464137914874413999[64] = 0;
   out_8464137914874413999[65] = 0;
   out_8464137914874413999[66] = dt;
   out_8464137914874413999[67] = 0;
   out_8464137914874413999[68] = 0;
   out_8464137914874413999[69] = 0;
   out_8464137914874413999[70] = 0;
   out_8464137914874413999[71] = 0;
   out_8464137914874413999[72] = 0;
   out_8464137914874413999[73] = 0;
   out_8464137914874413999[74] = 0;
   out_8464137914874413999[75] = 0;
   out_8464137914874413999[76] = 1;
   out_8464137914874413999[77] = 0;
   out_8464137914874413999[78] = 0;
   out_8464137914874413999[79] = 0;
   out_8464137914874413999[80] = 0;
   out_8464137914874413999[81] = 0;
   out_8464137914874413999[82] = 0;
   out_8464137914874413999[83] = 0;
   out_8464137914874413999[84] = 0;
   out_8464137914874413999[85] = dt;
   out_8464137914874413999[86] = 0;
   out_8464137914874413999[87] = 0;
   out_8464137914874413999[88] = 0;
   out_8464137914874413999[89] = 0;
   out_8464137914874413999[90] = 0;
   out_8464137914874413999[91] = 0;
   out_8464137914874413999[92] = 0;
   out_8464137914874413999[93] = 0;
   out_8464137914874413999[94] = 0;
   out_8464137914874413999[95] = 1;
   out_8464137914874413999[96] = 0;
   out_8464137914874413999[97] = 0;
   out_8464137914874413999[98] = 0;
   out_8464137914874413999[99] = 0;
   out_8464137914874413999[100] = 0;
   out_8464137914874413999[101] = 0;
   out_8464137914874413999[102] = 0;
   out_8464137914874413999[103] = 0;
   out_8464137914874413999[104] = dt;
   out_8464137914874413999[105] = 0;
   out_8464137914874413999[106] = 0;
   out_8464137914874413999[107] = 0;
   out_8464137914874413999[108] = 0;
   out_8464137914874413999[109] = 0;
   out_8464137914874413999[110] = 0;
   out_8464137914874413999[111] = 0;
   out_8464137914874413999[112] = 0;
   out_8464137914874413999[113] = 0;
   out_8464137914874413999[114] = 1;
   out_8464137914874413999[115] = 0;
   out_8464137914874413999[116] = 0;
   out_8464137914874413999[117] = 0;
   out_8464137914874413999[118] = 0;
   out_8464137914874413999[119] = 0;
   out_8464137914874413999[120] = 0;
   out_8464137914874413999[121] = 0;
   out_8464137914874413999[122] = 0;
   out_8464137914874413999[123] = 0;
   out_8464137914874413999[124] = 0;
   out_8464137914874413999[125] = 0;
   out_8464137914874413999[126] = 0;
   out_8464137914874413999[127] = 0;
   out_8464137914874413999[128] = 0;
   out_8464137914874413999[129] = 0;
   out_8464137914874413999[130] = 0;
   out_8464137914874413999[131] = 0;
   out_8464137914874413999[132] = 0;
   out_8464137914874413999[133] = 1;
   out_8464137914874413999[134] = 0;
   out_8464137914874413999[135] = 0;
   out_8464137914874413999[136] = 0;
   out_8464137914874413999[137] = 0;
   out_8464137914874413999[138] = 0;
   out_8464137914874413999[139] = 0;
   out_8464137914874413999[140] = 0;
   out_8464137914874413999[141] = 0;
   out_8464137914874413999[142] = 0;
   out_8464137914874413999[143] = 0;
   out_8464137914874413999[144] = 0;
   out_8464137914874413999[145] = 0;
   out_8464137914874413999[146] = 0;
   out_8464137914874413999[147] = 0;
   out_8464137914874413999[148] = 0;
   out_8464137914874413999[149] = 0;
   out_8464137914874413999[150] = 0;
   out_8464137914874413999[151] = 0;
   out_8464137914874413999[152] = 1;
   out_8464137914874413999[153] = 0;
   out_8464137914874413999[154] = 0;
   out_8464137914874413999[155] = 0;
   out_8464137914874413999[156] = 0;
   out_8464137914874413999[157] = 0;
   out_8464137914874413999[158] = 0;
   out_8464137914874413999[159] = 0;
   out_8464137914874413999[160] = 0;
   out_8464137914874413999[161] = 0;
   out_8464137914874413999[162] = 0;
   out_8464137914874413999[163] = 0;
   out_8464137914874413999[164] = 0;
   out_8464137914874413999[165] = 0;
   out_8464137914874413999[166] = 0;
   out_8464137914874413999[167] = 0;
   out_8464137914874413999[168] = 0;
   out_8464137914874413999[169] = 0;
   out_8464137914874413999[170] = 0;
   out_8464137914874413999[171] = 1;
   out_8464137914874413999[172] = 0;
   out_8464137914874413999[173] = 0;
   out_8464137914874413999[174] = 0;
   out_8464137914874413999[175] = 0;
   out_8464137914874413999[176] = 0;
   out_8464137914874413999[177] = 0;
   out_8464137914874413999[178] = 0;
   out_8464137914874413999[179] = 0;
   out_8464137914874413999[180] = 0;
   out_8464137914874413999[181] = 0;
   out_8464137914874413999[182] = 0;
   out_8464137914874413999[183] = 0;
   out_8464137914874413999[184] = 0;
   out_8464137914874413999[185] = 0;
   out_8464137914874413999[186] = 0;
   out_8464137914874413999[187] = 0;
   out_8464137914874413999[188] = 0;
   out_8464137914874413999[189] = 0;
   out_8464137914874413999[190] = 1;
   out_8464137914874413999[191] = 0;
   out_8464137914874413999[192] = 0;
   out_8464137914874413999[193] = 0;
   out_8464137914874413999[194] = 0;
   out_8464137914874413999[195] = 0;
   out_8464137914874413999[196] = 0;
   out_8464137914874413999[197] = 0;
   out_8464137914874413999[198] = 0;
   out_8464137914874413999[199] = 0;
   out_8464137914874413999[200] = 0;
   out_8464137914874413999[201] = 0;
   out_8464137914874413999[202] = 0;
   out_8464137914874413999[203] = 0;
   out_8464137914874413999[204] = 0;
   out_8464137914874413999[205] = 0;
   out_8464137914874413999[206] = 0;
   out_8464137914874413999[207] = 0;
   out_8464137914874413999[208] = 0;
   out_8464137914874413999[209] = 1;
   out_8464137914874413999[210] = 0;
   out_8464137914874413999[211] = 0;
   out_8464137914874413999[212] = 0;
   out_8464137914874413999[213] = 0;
   out_8464137914874413999[214] = 0;
   out_8464137914874413999[215] = 0;
   out_8464137914874413999[216] = 0;
   out_8464137914874413999[217] = 0;
   out_8464137914874413999[218] = 0;
   out_8464137914874413999[219] = 0;
   out_8464137914874413999[220] = 0;
   out_8464137914874413999[221] = 0;
   out_8464137914874413999[222] = 0;
   out_8464137914874413999[223] = 0;
   out_8464137914874413999[224] = 0;
   out_8464137914874413999[225] = 0;
   out_8464137914874413999[226] = 0;
   out_8464137914874413999[227] = 0;
   out_8464137914874413999[228] = 1;
   out_8464137914874413999[229] = 0;
   out_8464137914874413999[230] = 0;
   out_8464137914874413999[231] = 0;
   out_8464137914874413999[232] = 0;
   out_8464137914874413999[233] = 0;
   out_8464137914874413999[234] = 0;
   out_8464137914874413999[235] = 0;
   out_8464137914874413999[236] = 0;
   out_8464137914874413999[237] = 0;
   out_8464137914874413999[238] = 0;
   out_8464137914874413999[239] = 0;
   out_8464137914874413999[240] = 0;
   out_8464137914874413999[241] = 0;
   out_8464137914874413999[242] = 0;
   out_8464137914874413999[243] = 0;
   out_8464137914874413999[244] = 0;
   out_8464137914874413999[245] = 0;
   out_8464137914874413999[246] = 0;
   out_8464137914874413999[247] = 1;
   out_8464137914874413999[248] = 0;
   out_8464137914874413999[249] = 0;
   out_8464137914874413999[250] = 0;
   out_8464137914874413999[251] = 0;
   out_8464137914874413999[252] = 0;
   out_8464137914874413999[253] = 0;
   out_8464137914874413999[254] = 0;
   out_8464137914874413999[255] = 0;
   out_8464137914874413999[256] = 0;
   out_8464137914874413999[257] = 0;
   out_8464137914874413999[258] = 0;
   out_8464137914874413999[259] = 0;
   out_8464137914874413999[260] = 0;
   out_8464137914874413999[261] = 0;
   out_8464137914874413999[262] = 0;
   out_8464137914874413999[263] = 0;
   out_8464137914874413999[264] = 0;
   out_8464137914874413999[265] = 0;
   out_8464137914874413999[266] = 1;
   out_8464137914874413999[267] = 0;
   out_8464137914874413999[268] = 0;
   out_8464137914874413999[269] = 0;
   out_8464137914874413999[270] = 0;
   out_8464137914874413999[271] = 0;
   out_8464137914874413999[272] = 0;
   out_8464137914874413999[273] = 0;
   out_8464137914874413999[274] = 0;
   out_8464137914874413999[275] = 0;
   out_8464137914874413999[276] = 0;
   out_8464137914874413999[277] = 0;
   out_8464137914874413999[278] = 0;
   out_8464137914874413999[279] = 0;
   out_8464137914874413999[280] = 0;
   out_8464137914874413999[281] = 0;
   out_8464137914874413999[282] = 0;
   out_8464137914874413999[283] = 0;
   out_8464137914874413999[284] = 0;
   out_8464137914874413999[285] = 1;
   out_8464137914874413999[286] = 0;
   out_8464137914874413999[287] = 0;
   out_8464137914874413999[288] = 0;
   out_8464137914874413999[289] = 0;
   out_8464137914874413999[290] = 0;
   out_8464137914874413999[291] = 0;
   out_8464137914874413999[292] = 0;
   out_8464137914874413999[293] = 0;
   out_8464137914874413999[294] = 0;
   out_8464137914874413999[295] = 0;
   out_8464137914874413999[296] = 0;
   out_8464137914874413999[297] = 0;
   out_8464137914874413999[298] = 0;
   out_8464137914874413999[299] = 0;
   out_8464137914874413999[300] = 0;
   out_8464137914874413999[301] = 0;
   out_8464137914874413999[302] = 0;
   out_8464137914874413999[303] = 0;
   out_8464137914874413999[304] = 1;
   out_8464137914874413999[305] = 0;
   out_8464137914874413999[306] = 0;
   out_8464137914874413999[307] = 0;
   out_8464137914874413999[308] = 0;
   out_8464137914874413999[309] = 0;
   out_8464137914874413999[310] = 0;
   out_8464137914874413999[311] = 0;
   out_8464137914874413999[312] = 0;
   out_8464137914874413999[313] = 0;
   out_8464137914874413999[314] = 0;
   out_8464137914874413999[315] = 0;
   out_8464137914874413999[316] = 0;
   out_8464137914874413999[317] = 0;
   out_8464137914874413999[318] = 0;
   out_8464137914874413999[319] = 0;
   out_8464137914874413999[320] = 0;
   out_8464137914874413999[321] = 0;
   out_8464137914874413999[322] = 0;
   out_8464137914874413999[323] = 1;
}
void h_4(double *state, double *unused, double *out_6867211808814574533) {
   out_6867211808814574533[0] = state[6] + state[9];
   out_6867211808814574533[1] = state[7] + state[10];
   out_6867211808814574533[2] = state[8] + state[11];
}
void H_4(double *state, double *unused, double *out_1124207242241069802) {
   out_1124207242241069802[0] = 0;
   out_1124207242241069802[1] = 0;
   out_1124207242241069802[2] = 0;
   out_1124207242241069802[3] = 0;
   out_1124207242241069802[4] = 0;
   out_1124207242241069802[5] = 0;
   out_1124207242241069802[6] = 1;
   out_1124207242241069802[7] = 0;
   out_1124207242241069802[8] = 0;
   out_1124207242241069802[9] = 1;
   out_1124207242241069802[10] = 0;
   out_1124207242241069802[11] = 0;
   out_1124207242241069802[12] = 0;
   out_1124207242241069802[13] = 0;
   out_1124207242241069802[14] = 0;
   out_1124207242241069802[15] = 0;
   out_1124207242241069802[16] = 0;
   out_1124207242241069802[17] = 0;
   out_1124207242241069802[18] = 0;
   out_1124207242241069802[19] = 0;
   out_1124207242241069802[20] = 0;
   out_1124207242241069802[21] = 0;
   out_1124207242241069802[22] = 0;
   out_1124207242241069802[23] = 0;
   out_1124207242241069802[24] = 0;
   out_1124207242241069802[25] = 1;
   out_1124207242241069802[26] = 0;
   out_1124207242241069802[27] = 0;
   out_1124207242241069802[28] = 1;
   out_1124207242241069802[29] = 0;
   out_1124207242241069802[30] = 0;
   out_1124207242241069802[31] = 0;
   out_1124207242241069802[32] = 0;
   out_1124207242241069802[33] = 0;
   out_1124207242241069802[34] = 0;
   out_1124207242241069802[35] = 0;
   out_1124207242241069802[36] = 0;
   out_1124207242241069802[37] = 0;
   out_1124207242241069802[38] = 0;
   out_1124207242241069802[39] = 0;
   out_1124207242241069802[40] = 0;
   out_1124207242241069802[41] = 0;
   out_1124207242241069802[42] = 0;
   out_1124207242241069802[43] = 0;
   out_1124207242241069802[44] = 1;
   out_1124207242241069802[45] = 0;
   out_1124207242241069802[46] = 0;
   out_1124207242241069802[47] = 1;
   out_1124207242241069802[48] = 0;
   out_1124207242241069802[49] = 0;
   out_1124207242241069802[50] = 0;
   out_1124207242241069802[51] = 0;
   out_1124207242241069802[52] = 0;
   out_1124207242241069802[53] = 0;
}
void h_10(double *state, double *unused, double *out_6302404282439004399) {
   out_6302404282439004399[0] = 9.8100000000000005*sin(state[1]) - state[4]*state[8] + state[5]*state[7] + state[12] + state[15];
   out_6302404282439004399[1] = -9.8100000000000005*sin(state[0])*cos(state[1]) + state[3]*state[8] - state[5]*state[6] + state[13] + state[16];
   out_6302404282439004399[2] = -9.8100000000000005*cos(state[0])*cos(state[1]) - state[3]*state[7] + state[4]*state[6] + state[14] + state[17];
}
void H_10(double *state, double *unused, double *out_8521792719043079998) {
   out_8521792719043079998[0] = 0;
   out_8521792719043079998[1] = 9.8100000000000005*cos(state[1]);
   out_8521792719043079998[2] = 0;
   out_8521792719043079998[3] = 0;
   out_8521792719043079998[4] = -state[8];
   out_8521792719043079998[5] = state[7];
   out_8521792719043079998[6] = 0;
   out_8521792719043079998[7] = state[5];
   out_8521792719043079998[8] = -state[4];
   out_8521792719043079998[9] = 0;
   out_8521792719043079998[10] = 0;
   out_8521792719043079998[11] = 0;
   out_8521792719043079998[12] = 1;
   out_8521792719043079998[13] = 0;
   out_8521792719043079998[14] = 0;
   out_8521792719043079998[15] = 1;
   out_8521792719043079998[16] = 0;
   out_8521792719043079998[17] = 0;
   out_8521792719043079998[18] = -9.8100000000000005*cos(state[0])*cos(state[1]);
   out_8521792719043079998[19] = 9.8100000000000005*sin(state[0])*sin(state[1]);
   out_8521792719043079998[20] = 0;
   out_8521792719043079998[21] = state[8];
   out_8521792719043079998[22] = 0;
   out_8521792719043079998[23] = -state[6];
   out_8521792719043079998[24] = -state[5];
   out_8521792719043079998[25] = 0;
   out_8521792719043079998[26] = state[3];
   out_8521792719043079998[27] = 0;
   out_8521792719043079998[28] = 0;
   out_8521792719043079998[29] = 0;
   out_8521792719043079998[30] = 0;
   out_8521792719043079998[31] = 1;
   out_8521792719043079998[32] = 0;
   out_8521792719043079998[33] = 0;
   out_8521792719043079998[34] = 1;
   out_8521792719043079998[35] = 0;
   out_8521792719043079998[36] = 9.8100000000000005*sin(state[0])*cos(state[1]);
   out_8521792719043079998[37] = 9.8100000000000005*sin(state[1])*cos(state[0]);
   out_8521792719043079998[38] = 0;
   out_8521792719043079998[39] = -state[7];
   out_8521792719043079998[40] = state[6];
   out_8521792719043079998[41] = 0;
   out_8521792719043079998[42] = state[4];
   out_8521792719043079998[43] = -state[3];
   out_8521792719043079998[44] = 0;
   out_8521792719043079998[45] = 0;
   out_8521792719043079998[46] = 0;
   out_8521792719043079998[47] = 0;
   out_8521792719043079998[48] = 0;
   out_8521792719043079998[49] = 0;
   out_8521792719043079998[50] = 1;
   out_8521792719043079998[51] = 0;
   out_8521792719043079998[52] = 0;
   out_8521792719043079998[53] = 1;
}
void h_13(double *state, double *unused, double *out_3755491083597854401) {
   out_3755491083597854401[0] = state[3];
   out_3755491083597854401[1] = state[4];
   out_3755491083597854401[2] = state[5];
}
void H_13(double *state, double *unused, double *out_4336481067573402603) {
   out_4336481067573402603[0] = 0;
   out_4336481067573402603[1] = 0;
   out_4336481067573402603[2] = 0;
   out_4336481067573402603[3] = 1;
   out_4336481067573402603[4] = 0;
   out_4336481067573402603[5] = 0;
   out_4336481067573402603[6] = 0;
   out_4336481067573402603[7] = 0;
   out_4336481067573402603[8] = 0;
   out_4336481067573402603[9] = 0;
   out_4336481067573402603[10] = 0;
   out_4336481067573402603[11] = 0;
   out_4336481067573402603[12] = 0;
   out_4336481067573402603[13] = 0;
   out_4336481067573402603[14] = 0;
   out_4336481067573402603[15] = 0;
   out_4336481067573402603[16] = 0;
   out_4336481067573402603[17] = 0;
   out_4336481067573402603[18] = 0;
   out_4336481067573402603[19] = 0;
   out_4336481067573402603[20] = 0;
   out_4336481067573402603[21] = 0;
   out_4336481067573402603[22] = 1;
   out_4336481067573402603[23] = 0;
   out_4336481067573402603[24] = 0;
   out_4336481067573402603[25] = 0;
   out_4336481067573402603[26] = 0;
   out_4336481067573402603[27] = 0;
   out_4336481067573402603[28] = 0;
   out_4336481067573402603[29] = 0;
   out_4336481067573402603[30] = 0;
   out_4336481067573402603[31] = 0;
   out_4336481067573402603[32] = 0;
   out_4336481067573402603[33] = 0;
   out_4336481067573402603[34] = 0;
   out_4336481067573402603[35] = 0;
   out_4336481067573402603[36] = 0;
   out_4336481067573402603[37] = 0;
   out_4336481067573402603[38] = 0;
   out_4336481067573402603[39] = 0;
   out_4336481067573402603[40] = 0;
   out_4336481067573402603[41] = 1;
   out_4336481067573402603[42] = 0;
   out_4336481067573402603[43] = 0;
   out_4336481067573402603[44] = 0;
   out_4336481067573402603[45] = 0;
   out_4336481067573402603[46] = 0;
   out_4336481067573402603[47] = 0;
   out_4336481067573402603[48] = 0;
   out_4336481067573402603[49] = 0;
   out_4336481067573402603[50] = 0;
   out_4336481067573402603[51] = 0;
   out_4336481067573402603[52] = 0;
   out_4336481067573402603[53] = 0;
}
void h_14(double *state, double *unused, double *out_4171129885438629963) {
   out_4171129885438629963[0] = state[6];
   out_4171129885438629963[1] = state[7];
   out_4171129885438629963[2] = state[8];
}
void H_14(double *state, double *unused, double *out_5087448098580554331) {
   out_5087448098580554331[0] = 0;
   out_5087448098580554331[1] = 0;
   out_5087448098580554331[2] = 0;
   out_5087448098580554331[3] = 0;
   out_5087448098580554331[4] = 0;
   out_5087448098580554331[5] = 0;
   out_5087448098580554331[6] = 1;
   out_5087448098580554331[7] = 0;
   out_5087448098580554331[8] = 0;
   out_5087448098580554331[9] = 0;
   out_5087448098580554331[10] = 0;
   out_5087448098580554331[11] = 0;
   out_5087448098580554331[12] = 0;
   out_5087448098580554331[13] = 0;
   out_5087448098580554331[14] = 0;
   out_5087448098580554331[15] = 0;
   out_5087448098580554331[16] = 0;
   out_5087448098580554331[17] = 0;
   out_5087448098580554331[18] = 0;
   out_5087448098580554331[19] = 0;
   out_5087448098580554331[20] = 0;
   out_5087448098580554331[21] = 0;
   out_5087448098580554331[22] = 0;
   out_5087448098580554331[23] = 0;
   out_5087448098580554331[24] = 0;
   out_5087448098580554331[25] = 1;
   out_5087448098580554331[26] = 0;
   out_5087448098580554331[27] = 0;
   out_5087448098580554331[28] = 0;
   out_5087448098580554331[29] = 0;
   out_5087448098580554331[30] = 0;
   out_5087448098580554331[31] = 0;
   out_5087448098580554331[32] = 0;
   out_5087448098580554331[33] = 0;
   out_5087448098580554331[34] = 0;
   out_5087448098580554331[35] = 0;
   out_5087448098580554331[36] = 0;
   out_5087448098580554331[37] = 0;
   out_5087448098580554331[38] = 0;
   out_5087448098580554331[39] = 0;
   out_5087448098580554331[40] = 0;
   out_5087448098580554331[41] = 0;
   out_5087448098580554331[42] = 0;
   out_5087448098580554331[43] = 0;
   out_5087448098580554331[44] = 1;
   out_5087448098580554331[45] = 0;
   out_5087448098580554331[46] = 0;
   out_5087448098580554331[47] = 0;
   out_5087448098580554331[48] = 0;
   out_5087448098580554331[49] = 0;
   out_5087448098580554331[50] = 0;
   out_5087448098580554331[51] = 0;
   out_5087448098580554331[52] = 0;
   out_5087448098580554331[53] = 0;
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
void pose_err_fun(double *nom_x, double *delta_x, double *out_4349191364545344677) {
  err_fun(nom_x, delta_x, out_4349191364545344677);
}
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_6050387772030197335) {
  inv_err_fun(nom_x, true_x, out_6050387772030197335);
}
void pose_H_mod_fun(double *state, double *out_7888777177672614690) {
  H_mod_fun(state, out_7888777177672614690);
}
void pose_f_fun(double *state, double dt, double *out_2236332295548027163) {
  f_fun(state,  dt, out_2236332295548027163);
}
void pose_F_fun(double *state, double dt, double *out_8464137914874413999) {
  F_fun(state,  dt, out_8464137914874413999);
}
void pose_h_4(double *state, double *unused, double *out_6867211808814574533) {
  h_4(state, unused, out_6867211808814574533);
}
void pose_H_4(double *state, double *unused, double *out_1124207242241069802) {
  H_4(state, unused, out_1124207242241069802);
}
void pose_h_10(double *state, double *unused, double *out_6302404282439004399) {
  h_10(state, unused, out_6302404282439004399);
}
void pose_H_10(double *state, double *unused, double *out_8521792719043079998) {
  H_10(state, unused, out_8521792719043079998);
}
void pose_h_13(double *state, double *unused, double *out_3755491083597854401) {
  h_13(state, unused, out_3755491083597854401);
}
void pose_H_13(double *state, double *unused, double *out_4336481067573402603) {
  H_13(state, unused, out_4336481067573402603);
}
void pose_h_14(double *state, double *unused, double *out_4171129885438629963) {
  h_14(state, unused, out_4171129885438629963);
}
void pose_H_14(double *state, double *unused, double *out_5087448098580554331) {
  H_14(state, unused, out_5087448098580554331);
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
