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
void err_fun(double *nom_x, double *delta_x, double *out_8942860553158829980) {
   out_8942860553158829980[0] = delta_x[0] + nom_x[0];
   out_8942860553158829980[1] = delta_x[1] + nom_x[1];
   out_8942860553158829980[2] = delta_x[2] + nom_x[2];
   out_8942860553158829980[3] = delta_x[3] + nom_x[3];
   out_8942860553158829980[4] = delta_x[4] + nom_x[4];
   out_8942860553158829980[5] = delta_x[5] + nom_x[5];
   out_8942860553158829980[6] = delta_x[6] + nom_x[6];
   out_8942860553158829980[7] = delta_x[7] + nom_x[7];
   out_8942860553158829980[8] = delta_x[8] + nom_x[8];
   out_8942860553158829980[9] = delta_x[9] + nom_x[9];
   out_8942860553158829980[10] = delta_x[10] + nom_x[10];
   out_8942860553158829980[11] = delta_x[11] + nom_x[11];
   out_8942860553158829980[12] = delta_x[12] + nom_x[12];
   out_8942860553158829980[13] = delta_x[13] + nom_x[13];
   out_8942860553158829980[14] = delta_x[14] + nom_x[14];
   out_8942860553158829980[15] = delta_x[15] + nom_x[15];
   out_8942860553158829980[16] = delta_x[16] + nom_x[16];
   out_8942860553158829980[17] = delta_x[17] + nom_x[17];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_2254641408306223903) {
   out_2254641408306223903[0] = -nom_x[0] + true_x[0];
   out_2254641408306223903[1] = -nom_x[1] + true_x[1];
   out_2254641408306223903[2] = -nom_x[2] + true_x[2];
   out_2254641408306223903[3] = -nom_x[3] + true_x[3];
   out_2254641408306223903[4] = -nom_x[4] + true_x[4];
   out_2254641408306223903[5] = -nom_x[5] + true_x[5];
   out_2254641408306223903[6] = -nom_x[6] + true_x[6];
   out_2254641408306223903[7] = -nom_x[7] + true_x[7];
   out_2254641408306223903[8] = -nom_x[8] + true_x[8];
   out_2254641408306223903[9] = -nom_x[9] + true_x[9];
   out_2254641408306223903[10] = -nom_x[10] + true_x[10];
   out_2254641408306223903[11] = -nom_x[11] + true_x[11];
   out_2254641408306223903[12] = -nom_x[12] + true_x[12];
   out_2254641408306223903[13] = -nom_x[13] + true_x[13];
   out_2254641408306223903[14] = -nom_x[14] + true_x[14];
   out_2254641408306223903[15] = -nom_x[15] + true_x[15];
   out_2254641408306223903[16] = -nom_x[16] + true_x[16];
   out_2254641408306223903[17] = -nom_x[17] + true_x[17];
}
void H_mod_fun(double *state, double *out_76292315910108330) {
   out_76292315910108330[0] = 1.0;
   out_76292315910108330[1] = 0.0;
   out_76292315910108330[2] = 0.0;
   out_76292315910108330[3] = 0.0;
   out_76292315910108330[4] = 0.0;
   out_76292315910108330[5] = 0.0;
   out_76292315910108330[6] = 0.0;
   out_76292315910108330[7] = 0.0;
   out_76292315910108330[8] = 0.0;
   out_76292315910108330[9] = 0.0;
   out_76292315910108330[10] = 0.0;
   out_76292315910108330[11] = 0.0;
   out_76292315910108330[12] = 0.0;
   out_76292315910108330[13] = 0.0;
   out_76292315910108330[14] = 0.0;
   out_76292315910108330[15] = 0.0;
   out_76292315910108330[16] = 0.0;
   out_76292315910108330[17] = 0.0;
   out_76292315910108330[18] = 0.0;
   out_76292315910108330[19] = 1.0;
   out_76292315910108330[20] = 0.0;
   out_76292315910108330[21] = 0.0;
   out_76292315910108330[22] = 0.0;
   out_76292315910108330[23] = 0.0;
   out_76292315910108330[24] = 0.0;
   out_76292315910108330[25] = 0.0;
   out_76292315910108330[26] = 0.0;
   out_76292315910108330[27] = 0.0;
   out_76292315910108330[28] = 0.0;
   out_76292315910108330[29] = 0.0;
   out_76292315910108330[30] = 0.0;
   out_76292315910108330[31] = 0.0;
   out_76292315910108330[32] = 0.0;
   out_76292315910108330[33] = 0.0;
   out_76292315910108330[34] = 0.0;
   out_76292315910108330[35] = 0.0;
   out_76292315910108330[36] = 0.0;
   out_76292315910108330[37] = 0.0;
   out_76292315910108330[38] = 1.0;
   out_76292315910108330[39] = 0.0;
   out_76292315910108330[40] = 0.0;
   out_76292315910108330[41] = 0.0;
   out_76292315910108330[42] = 0.0;
   out_76292315910108330[43] = 0.0;
   out_76292315910108330[44] = 0.0;
   out_76292315910108330[45] = 0.0;
   out_76292315910108330[46] = 0.0;
   out_76292315910108330[47] = 0.0;
   out_76292315910108330[48] = 0.0;
   out_76292315910108330[49] = 0.0;
   out_76292315910108330[50] = 0.0;
   out_76292315910108330[51] = 0.0;
   out_76292315910108330[52] = 0.0;
   out_76292315910108330[53] = 0.0;
   out_76292315910108330[54] = 0.0;
   out_76292315910108330[55] = 0.0;
   out_76292315910108330[56] = 0.0;
   out_76292315910108330[57] = 1.0;
   out_76292315910108330[58] = 0.0;
   out_76292315910108330[59] = 0.0;
   out_76292315910108330[60] = 0.0;
   out_76292315910108330[61] = 0.0;
   out_76292315910108330[62] = 0.0;
   out_76292315910108330[63] = 0.0;
   out_76292315910108330[64] = 0.0;
   out_76292315910108330[65] = 0.0;
   out_76292315910108330[66] = 0.0;
   out_76292315910108330[67] = 0.0;
   out_76292315910108330[68] = 0.0;
   out_76292315910108330[69] = 0.0;
   out_76292315910108330[70] = 0.0;
   out_76292315910108330[71] = 0.0;
   out_76292315910108330[72] = 0.0;
   out_76292315910108330[73] = 0.0;
   out_76292315910108330[74] = 0.0;
   out_76292315910108330[75] = 0.0;
   out_76292315910108330[76] = 1.0;
   out_76292315910108330[77] = 0.0;
   out_76292315910108330[78] = 0.0;
   out_76292315910108330[79] = 0.0;
   out_76292315910108330[80] = 0.0;
   out_76292315910108330[81] = 0.0;
   out_76292315910108330[82] = 0.0;
   out_76292315910108330[83] = 0.0;
   out_76292315910108330[84] = 0.0;
   out_76292315910108330[85] = 0.0;
   out_76292315910108330[86] = 0.0;
   out_76292315910108330[87] = 0.0;
   out_76292315910108330[88] = 0.0;
   out_76292315910108330[89] = 0.0;
   out_76292315910108330[90] = 0.0;
   out_76292315910108330[91] = 0.0;
   out_76292315910108330[92] = 0.0;
   out_76292315910108330[93] = 0.0;
   out_76292315910108330[94] = 0.0;
   out_76292315910108330[95] = 1.0;
   out_76292315910108330[96] = 0.0;
   out_76292315910108330[97] = 0.0;
   out_76292315910108330[98] = 0.0;
   out_76292315910108330[99] = 0.0;
   out_76292315910108330[100] = 0.0;
   out_76292315910108330[101] = 0.0;
   out_76292315910108330[102] = 0.0;
   out_76292315910108330[103] = 0.0;
   out_76292315910108330[104] = 0.0;
   out_76292315910108330[105] = 0.0;
   out_76292315910108330[106] = 0.0;
   out_76292315910108330[107] = 0.0;
   out_76292315910108330[108] = 0.0;
   out_76292315910108330[109] = 0.0;
   out_76292315910108330[110] = 0.0;
   out_76292315910108330[111] = 0.0;
   out_76292315910108330[112] = 0.0;
   out_76292315910108330[113] = 0.0;
   out_76292315910108330[114] = 1.0;
   out_76292315910108330[115] = 0.0;
   out_76292315910108330[116] = 0.0;
   out_76292315910108330[117] = 0.0;
   out_76292315910108330[118] = 0.0;
   out_76292315910108330[119] = 0.0;
   out_76292315910108330[120] = 0.0;
   out_76292315910108330[121] = 0.0;
   out_76292315910108330[122] = 0.0;
   out_76292315910108330[123] = 0.0;
   out_76292315910108330[124] = 0.0;
   out_76292315910108330[125] = 0.0;
   out_76292315910108330[126] = 0.0;
   out_76292315910108330[127] = 0.0;
   out_76292315910108330[128] = 0.0;
   out_76292315910108330[129] = 0.0;
   out_76292315910108330[130] = 0.0;
   out_76292315910108330[131] = 0.0;
   out_76292315910108330[132] = 0.0;
   out_76292315910108330[133] = 1.0;
   out_76292315910108330[134] = 0.0;
   out_76292315910108330[135] = 0.0;
   out_76292315910108330[136] = 0.0;
   out_76292315910108330[137] = 0.0;
   out_76292315910108330[138] = 0.0;
   out_76292315910108330[139] = 0.0;
   out_76292315910108330[140] = 0.0;
   out_76292315910108330[141] = 0.0;
   out_76292315910108330[142] = 0.0;
   out_76292315910108330[143] = 0.0;
   out_76292315910108330[144] = 0.0;
   out_76292315910108330[145] = 0.0;
   out_76292315910108330[146] = 0.0;
   out_76292315910108330[147] = 0.0;
   out_76292315910108330[148] = 0.0;
   out_76292315910108330[149] = 0.0;
   out_76292315910108330[150] = 0.0;
   out_76292315910108330[151] = 0.0;
   out_76292315910108330[152] = 1.0;
   out_76292315910108330[153] = 0.0;
   out_76292315910108330[154] = 0.0;
   out_76292315910108330[155] = 0.0;
   out_76292315910108330[156] = 0.0;
   out_76292315910108330[157] = 0.0;
   out_76292315910108330[158] = 0.0;
   out_76292315910108330[159] = 0.0;
   out_76292315910108330[160] = 0.0;
   out_76292315910108330[161] = 0.0;
   out_76292315910108330[162] = 0.0;
   out_76292315910108330[163] = 0.0;
   out_76292315910108330[164] = 0.0;
   out_76292315910108330[165] = 0.0;
   out_76292315910108330[166] = 0.0;
   out_76292315910108330[167] = 0.0;
   out_76292315910108330[168] = 0.0;
   out_76292315910108330[169] = 0.0;
   out_76292315910108330[170] = 0.0;
   out_76292315910108330[171] = 1.0;
   out_76292315910108330[172] = 0.0;
   out_76292315910108330[173] = 0.0;
   out_76292315910108330[174] = 0.0;
   out_76292315910108330[175] = 0.0;
   out_76292315910108330[176] = 0.0;
   out_76292315910108330[177] = 0.0;
   out_76292315910108330[178] = 0.0;
   out_76292315910108330[179] = 0.0;
   out_76292315910108330[180] = 0.0;
   out_76292315910108330[181] = 0.0;
   out_76292315910108330[182] = 0.0;
   out_76292315910108330[183] = 0.0;
   out_76292315910108330[184] = 0.0;
   out_76292315910108330[185] = 0.0;
   out_76292315910108330[186] = 0.0;
   out_76292315910108330[187] = 0.0;
   out_76292315910108330[188] = 0.0;
   out_76292315910108330[189] = 0.0;
   out_76292315910108330[190] = 1.0;
   out_76292315910108330[191] = 0.0;
   out_76292315910108330[192] = 0.0;
   out_76292315910108330[193] = 0.0;
   out_76292315910108330[194] = 0.0;
   out_76292315910108330[195] = 0.0;
   out_76292315910108330[196] = 0.0;
   out_76292315910108330[197] = 0.0;
   out_76292315910108330[198] = 0.0;
   out_76292315910108330[199] = 0.0;
   out_76292315910108330[200] = 0.0;
   out_76292315910108330[201] = 0.0;
   out_76292315910108330[202] = 0.0;
   out_76292315910108330[203] = 0.0;
   out_76292315910108330[204] = 0.0;
   out_76292315910108330[205] = 0.0;
   out_76292315910108330[206] = 0.0;
   out_76292315910108330[207] = 0.0;
   out_76292315910108330[208] = 0.0;
   out_76292315910108330[209] = 1.0;
   out_76292315910108330[210] = 0.0;
   out_76292315910108330[211] = 0.0;
   out_76292315910108330[212] = 0.0;
   out_76292315910108330[213] = 0.0;
   out_76292315910108330[214] = 0.0;
   out_76292315910108330[215] = 0.0;
   out_76292315910108330[216] = 0.0;
   out_76292315910108330[217] = 0.0;
   out_76292315910108330[218] = 0.0;
   out_76292315910108330[219] = 0.0;
   out_76292315910108330[220] = 0.0;
   out_76292315910108330[221] = 0.0;
   out_76292315910108330[222] = 0.0;
   out_76292315910108330[223] = 0.0;
   out_76292315910108330[224] = 0.0;
   out_76292315910108330[225] = 0.0;
   out_76292315910108330[226] = 0.0;
   out_76292315910108330[227] = 0.0;
   out_76292315910108330[228] = 1.0;
   out_76292315910108330[229] = 0.0;
   out_76292315910108330[230] = 0.0;
   out_76292315910108330[231] = 0.0;
   out_76292315910108330[232] = 0.0;
   out_76292315910108330[233] = 0.0;
   out_76292315910108330[234] = 0.0;
   out_76292315910108330[235] = 0.0;
   out_76292315910108330[236] = 0.0;
   out_76292315910108330[237] = 0.0;
   out_76292315910108330[238] = 0.0;
   out_76292315910108330[239] = 0.0;
   out_76292315910108330[240] = 0.0;
   out_76292315910108330[241] = 0.0;
   out_76292315910108330[242] = 0.0;
   out_76292315910108330[243] = 0.0;
   out_76292315910108330[244] = 0.0;
   out_76292315910108330[245] = 0.0;
   out_76292315910108330[246] = 0.0;
   out_76292315910108330[247] = 1.0;
   out_76292315910108330[248] = 0.0;
   out_76292315910108330[249] = 0.0;
   out_76292315910108330[250] = 0.0;
   out_76292315910108330[251] = 0.0;
   out_76292315910108330[252] = 0.0;
   out_76292315910108330[253] = 0.0;
   out_76292315910108330[254] = 0.0;
   out_76292315910108330[255] = 0.0;
   out_76292315910108330[256] = 0.0;
   out_76292315910108330[257] = 0.0;
   out_76292315910108330[258] = 0.0;
   out_76292315910108330[259] = 0.0;
   out_76292315910108330[260] = 0.0;
   out_76292315910108330[261] = 0.0;
   out_76292315910108330[262] = 0.0;
   out_76292315910108330[263] = 0.0;
   out_76292315910108330[264] = 0.0;
   out_76292315910108330[265] = 0.0;
   out_76292315910108330[266] = 1.0;
   out_76292315910108330[267] = 0.0;
   out_76292315910108330[268] = 0.0;
   out_76292315910108330[269] = 0.0;
   out_76292315910108330[270] = 0.0;
   out_76292315910108330[271] = 0.0;
   out_76292315910108330[272] = 0.0;
   out_76292315910108330[273] = 0.0;
   out_76292315910108330[274] = 0.0;
   out_76292315910108330[275] = 0.0;
   out_76292315910108330[276] = 0.0;
   out_76292315910108330[277] = 0.0;
   out_76292315910108330[278] = 0.0;
   out_76292315910108330[279] = 0.0;
   out_76292315910108330[280] = 0.0;
   out_76292315910108330[281] = 0.0;
   out_76292315910108330[282] = 0.0;
   out_76292315910108330[283] = 0.0;
   out_76292315910108330[284] = 0.0;
   out_76292315910108330[285] = 1.0;
   out_76292315910108330[286] = 0.0;
   out_76292315910108330[287] = 0.0;
   out_76292315910108330[288] = 0.0;
   out_76292315910108330[289] = 0.0;
   out_76292315910108330[290] = 0.0;
   out_76292315910108330[291] = 0.0;
   out_76292315910108330[292] = 0.0;
   out_76292315910108330[293] = 0.0;
   out_76292315910108330[294] = 0.0;
   out_76292315910108330[295] = 0.0;
   out_76292315910108330[296] = 0.0;
   out_76292315910108330[297] = 0.0;
   out_76292315910108330[298] = 0.0;
   out_76292315910108330[299] = 0.0;
   out_76292315910108330[300] = 0.0;
   out_76292315910108330[301] = 0.0;
   out_76292315910108330[302] = 0.0;
   out_76292315910108330[303] = 0.0;
   out_76292315910108330[304] = 1.0;
   out_76292315910108330[305] = 0.0;
   out_76292315910108330[306] = 0.0;
   out_76292315910108330[307] = 0.0;
   out_76292315910108330[308] = 0.0;
   out_76292315910108330[309] = 0.0;
   out_76292315910108330[310] = 0.0;
   out_76292315910108330[311] = 0.0;
   out_76292315910108330[312] = 0.0;
   out_76292315910108330[313] = 0.0;
   out_76292315910108330[314] = 0.0;
   out_76292315910108330[315] = 0.0;
   out_76292315910108330[316] = 0.0;
   out_76292315910108330[317] = 0.0;
   out_76292315910108330[318] = 0.0;
   out_76292315910108330[319] = 0.0;
   out_76292315910108330[320] = 0.0;
   out_76292315910108330[321] = 0.0;
   out_76292315910108330[322] = 0.0;
   out_76292315910108330[323] = 1.0;
}
void f_fun(double *state, double dt, double *out_1412378285720339068) {
   out_1412378285720339068[0] = atan2((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), -(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]));
   out_1412378285720339068[1] = asin(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]));
   out_1412378285720339068[2] = atan2(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), -(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]));
   out_1412378285720339068[3] = dt*state[12] + state[3];
   out_1412378285720339068[4] = dt*state[13] + state[4];
   out_1412378285720339068[5] = dt*state[14] + state[5];
   out_1412378285720339068[6] = state[6];
   out_1412378285720339068[7] = state[7];
   out_1412378285720339068[8] = state[8];
   out_1412378285720339068[9] = state[9];
   out_1412378285720339068[10] = state[10];
   out_1412378285720339068[11] = state[11];
   out_1412378285720339068[12] = state[12];
   out_1412378285720339068[13] = state[13];
   out_1412378285720339068[14] = state[14];
   out_1412378285720339068[15] = state[15];
   out_1412378285720339068[16] = state[16];
   out_1412378285720339068[17] = state[17];
}
void F_fun(double *state, double dt, double *out_5059879879978958987) {
   out_5059879879978958987[0] = ((-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*cos(state[0])*cos(state[1]) - sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*cos(state[0])*cos(state[1]) - sin(dt*state[6])*sin(state[0])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_5059879879978958987[1] = ((-sin(dt*state[6])*sin(dt*state[8]) - sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*cos(state[1]) - (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*sin(state[1]) - sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(state[0]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*sin(state[1]) + (-sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) + sin(dt*state[8])*cos(dt*state[6]))*cos(state[1]) - sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(state[0]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_5059879879978958987[2] = 0;
   out_5059879879978958987[3] = 0;
   out_5059879879978958987[4] = 0;
   out_5059879879978958987[5] = 0;
   out_5059879879978958987[6] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(dt*cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) - dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_5059879879978958987[7] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*sin(dt*state[7])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[6])*sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) - dt*sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[7])*cos(dt*state[6])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[8])*sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]) - dt*sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_5059879879978958987[8] = ((dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((dt*sin(dt*state[6])*sin(dt*state[8]) + dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_5059879879978958987[9] = 0;
   out_5059879879978958987[10] = 0;
   out_5059879879978958987[11] = 0;
   out_5059879879978958987[12] = 0;
   out_5059879879978958987[13] = 0;
   out_5059879879978958987[14] = 0;
   out_5059879879978958987[15] = 0;
   out_5059879879978958987[16] = 0;
   out_5059879879978958987[17] = 0;
   out_5059879879978958987[18] = (-sin(dt*state[7])*sin(state[0])*cos(state[1]) - sin(dt*state[8])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_5059879879978958987[19] = (-sin(dt*state[7])*sin(state[1])*cos(state[0]) + sin(dt*state[8])*sin(state[0])*sin(state[1])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_5059879879978958987[20] = 0;
   out_5059879879978958987[21] = 0;
   out_5059879879978958987[22] = 0;
   out_5059879879978958987[23] = 0;
   out_5059879879978958987[24] = 0;
   out_5059879879978958987[25] = (dt*sin(dt*state[7])*sin(dt*state[8])*sin(state[0])*cos(state[1]) - dt*sin(dt*state[7])*sin(state[1])*cos(dt*state[8]) + dt*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_5059879879978958987[26] = (-dt*sin(dt*state[8])*sin(state[1])*cos(dt*state[7]) - dt*sin(state[0])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_5059879879978958987[27] = 0;
   out_5059879879978958987[28] = 0;
   out_5059879879978958987[29] = 0;
   out_5059879879978958987[30] = 0;
   out_5059879879978958987[31] = 0;
   out_5059879879978958987[32] = 0;
   out_5059879879978958987[33] = 0;
   out_5059879879978958987[34] = 0;
   out_5059879879978958987[35] = 0;
   out_5059879879978958987[36] = ((sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_5059879879978958987[37] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-sin(dt*state[7])*sin(state[2])*cos(state[0])*cos(state[1]) + sin(dt*state[8])*sin(state[0])*sin(state[2])*cos(dt*state[7])*cos(state[1]) - sin(state[1])*sin(state[2])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(-sin(dt*state[7])*cos(state[0])*cos(state[1])*cos(state[2]) + sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1])*cos(state[2]) - sin(state[1])*cos(dt*state[7])*cos(dt*state[8])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_5059879879978958987[38] = ((-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (-sin(state[0])*sin(state[1])*sin(state[2]) - cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_5059879879978958987[39] = 0;
   out_5059879879978958987[40] = 0;
   out_5059879879978958987[41] = 0;
   out_5059879879978958987[42] = 0;
   out_5059879879978958987[43] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(dt*(sin(state[0])*cos(state[2]) - sin(state[1])*sin(state[2])*cos(state[0]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*sin(state[2])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(dt*(-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_5059879879978958987[44] = (dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*sin(state[2])*cos(dt*state[7])*cos(state[1]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + (dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[7])*cos(state[1])*cos(state[2]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_5059879879978958987[45] = 0;
   out_5059879879978958987[46] = 0;
   out_5059879879978958987[47] = 0;
   out_5059879879978958987[48] = 0;
   out_5059879879978958987[49] = 0;
   out_5059879879978958987[50] = 0;
   out_5059879879978958987[51] = 0;
   out_5059879879978958987[52] = 0;
   out_5059879879978958987[53] = 0;
   out_5059879879978958987[54] = 0;
   out_5059879879978958987[55] = 0;
   out_5059879879978958987[56] = 0;
   out_5059879879978958987[57] = 1;
   out_5059879879978958987[58] = 0;
   out_5059879879978958987[59] = 0;
   out_5059879879978958987[60] = 0;
   out_5059879879978958987[61] = 0;
   out_5059879879978958987[62] = 0;
   out_5059879879978958987[63] = 0;
   out_5059879879978958987[64] = 0;
   out_5059879879978958987[65] = 0;
   out_5059879879978958987[66] = dt;
   out_5059879879978958987[67] = 0;
   out_5059879879978958987[68] = 0;
   out_5059879879978958987[69] = 0;
   out_5059879879978958987[70] = 0;
   out_5059879879978958987[71] = 0;
   out_5059879879978958987[72] = 0;
   out_5059879879978958987[73] = 0;
   out_5059879879978958987[74] = 0;
   out_5059879879978958987[75] = 0;
   out_5059879879978958987[76] = 1;
   out_5059879879978958987[77] = 0;
   out_5059879879978958987[78] = 0;
   out_5059879879978958987[79] = 0;
   out_5059879879978958987[80] = 0;
   out_5059879879978958987[81] = 0;
   out_5059879879978958987[82] = 0;
   out_5059879879978958987[83] = 0;
   out_5059879879978958987[84] = 0;
   out_5059879879978958987[85] = dt;
   out_5059879879978958987[86] = 0;
   out_5059879879978958987[87] = 0;
   out_5059879879978958987[88] = 0;
   out_5059879879978958987[89] = 0;
   out_5059879879978958987[90] = 0;
   out_5059879879978958987[91] = 0;
   out_5059879879978958987[92] = 0;
   out_5059879879978958987[93] = 0;
   out_5059879879978958987[94] = 0;
   out_5059879879978958987[95] = 1;
   out_5059879879978958987[96] = 0;
   out_5059879879978958987[97] = 0;
   out_5059879879978958987[98] = 0;
   out_5059879879978958987[99] = 0;
   out_5059879879978958987[100] = 0;
   out_5059879879978958987[101] = 0;
   out_5059879879978958987[102] = 0;
   out_5059879879978958987[103] = 0;
   out_5059879879978958987[104] = dt;
   out_5059879879978958987[105] = 0;
   out_5059879879978958987[106] = 0;
   out_5059879879978958987[107] = 0;
   out_5059879879978958987[108] = 0;
   out_5059879879978958987[109] = 0;
   out_5059879879978958987[110] = 0;
   out_5059879879978958987[111] = 0;
   out_5059879879978958987[112] = 0;
   out_5059879879978958987[113] = 0;
   out_5059879879978958987[114] = 1;
   out_5059879879978958987[115] = 0;
   out_5059879879978958987[116] = 0;
   out_5059879879978958987[117] = 0;
   out_5059879879978958987[118] = 0;
   out_5059879879978958987[119] = 0;
   out_5059879879978958987[120] = 0;
   out_5059879879978958987[121] = 0;
   out_5059879879978958987[122] = 0;
   out_5059879879978958987[123] = 0;
   out_5059879879978958987[124] = 0;
   out_5059879879978958987[125] = 0;
   out_5059879879978958987[126] = 0;
   out_5059879879978958987[127] = 0;
   out_5059879879978958987[128] = 0;
   out_5059879879978958987[129] = 0;
   out_5059879879978958987[130] = 0;
   out_5059879879978958987[131] = 0;
   out_5059879879978958987[132] = 0;
   out_5059879879978958987[133] = 1;
   out_5059879879978958987[134] = 0;
   out_5059879879978958987[135] = 0;
   out_5059879879978958987[136] = 0;
   out_5059879879978958987[137] = 0;
   out_5059879879978958987[138] = 0;
   out_5059879879978958987[139] = 0;
   out_5059879879978958987[140] = 0;
   out_5059879879978958987[141] = 0;
   out_5059879879978958987[142] = 0;
   out_5059879879978958987[143] = 0;
   out_5059879879978958987[144] = 0;
   out_5059879879978958987[145] = 0;
   out_5059879879978958987[146] = 0;
   out_5059879879978958987[147] = 0;
   out_5059879879978958987[148] = 0;
   out_5059879879978958987[149] = 0;
   out_5059879879978958987[150] = 0;
   out_5059879879978958987[151] = 0;
   out_5059879879978958987[152] = 1;
   out_5059879879978958987[153] = 0;
   out_5059879879978958987[154] = 0;
   out_5059879879978958987[155] = 0;
   out_5059879879978958987[156] = 0;
   out_5059879879978958987[157] = 0;
   out_5059879879978958987[158] = 0;
   out_5059879879978958987[159] = 0;
   out_5059879879978958987[160] = 0;
   out_5059879879978958987[161] = 0;
   out_5059879879978958987[162] = 0;
   out_5059879879978958987[163] = 0;
   out_5059879879978958987[164] = 0;
   out_5059879879978958987[165] = 0;
   out_5059879879978958987[166] = 0;
   out_5059879879978958987[167] = 0;
   out_5059879879978958987[168] = 0;
   out_5059879879978958987[169] = 0;
   out_5059879879978958987[170] = 0;
   out_5059879879978958987[171] = 1;
   out_5059879879978958987[172] = 0;
   out_5059879879978958987[173] = 0;
   out_5059879879978958987[174] = 0;
   out_5059879879978958987[175] = 0;
   out_5059879879978958987[176] = 0;
   out_5059879879978958987[177] = 0;
   out_5059879879978958987[178] = 0;
   out_5059879879978958987[179] = 0;
   out_5059879879978958987[180] = 0;
   out_5059879879978958987[181] = 0;
   out_5059879879978958987[182] = 0;
   out_5059879879978958987[183] = 0;
   out_5059879879978958987[184] = 0;
   out_5059879879978958987[185] = 0;
   out_5059879879978958987[186] = 0;
   out_5059879879978958987[187] = 0;
   out_5059879879978958987[188] = 0;
   out_5059879879978958987[189] = 0;
   out_5059879879978958987[190] = 1;
   out_5059879879978958987[191] = 0;
   out_5059879879978958987[192] = 0;
   out_5059879879978958987[193] = 0;
   out_5059879879978958987[194] = 0;
   out_5059879879978958987[195] = 0;
   out_5059879879978958987[196] = 0;
   out_5059879879978958987[197] = 0;
   out_5059879879978958987[198] = 0;
   out_5059879879978958987[199] = 0;
   out_5059879879978958987[200] = 0;
   out_5059879879978958987[201] = 0;
   out_5059879879978958987[202] = 0;
   out_5059879879978958987[203] = 0;
   out_5059879879978958987[204] = 0;
   out_5059879879978958987[205] = 0;
   out_5059879879978958987[206] = 0;
   out_5059879879978958987[207] = 0;
   out_5059879879978958987[208] = 0;
   out_5059879879978958987[209] = 1;
   out_5059879879978958987[210] = 0;
   out_5059879879978958987[211] = 0;
   out_5059879879978958987[212] = 0;
   out_5059879879978958987[213] = 0;
   out_5059879879978958987[214] = 0;
   out_5059879879978958987[215] = 0;
   out_5059879879978958987[216] = 0;
   out_5059879879978958987[217] = 0;
   out_5059879879978958987[218] = 0;
   out_5059879879978958987[219] = 0;
   out_5059879879978958987[220] = 0;
   out_5059879879978958987[221] = 0;
   out_5059879879978958987[222] = 0;
   out_5059879879978958987[223] = 0;
   out_5059879879978958987[224] = 0;
   out_5059879879978958987[225] = 0;
   out_5059879879978958987[226] = 0;
   out_5059879879978958987[227] = 0;
   out_5059879879978958987[228] = 1;
   out_5059879879978958987[229] = 0;
   out_5059879879978958987[230] = 0;
   out_5059879879978958987[231] = 0;
   out_5059879879978958987[232] = 0;
   out_5059879879978958987[233] = 0;
   out_5059879879978958987[234] = 0;
   out_5059879879978958987[235] = 0;
   out_5059879879978958987[236] = 0;
   out_5059879879978958987[237] = 0;
   out_5059879879978958987[238] = 0;
   out_5059879879978958987[239] = 0;
   out_5059879879978958987[240] = 0;
   out_5059879879978958987[241] = 0;
   out_5059879879978958987[242] = 0;
   out_5059879879978958987[243] = 0;
   out_5059879879978958987[244] = 0;
   out_5059879879978958987[245] = 0;
   out_5059879879978958987[246] = 0;
   out_5059879879978958987[247] = 1;
   out_5059879879978958987[248] = 0;
   out_5059879879978958987[249] = 0;
   out_5059879879978958987[250] = 0;
   out_5059879879978958987[251] = 0;
   out_5059879879978958987[252] = 0;
   out_5059879879978958987[253] = 0;
   out_5059879879978958987[254] = 0;
   out_5059879879978958987[255] = 0;
   out_5059879879978958987[256] = 0;
   out_5059879879978958987[257] = 0;
   out_5059879879978958987[258] = 0;
   out_5059879879978958987[259] = 0;
   out_5059879879978958987[260] = 0;
   out_5059879879978958987[261] = 0;
   out_5059879879978958987[262] = 0;
   out_5059879879978958987[263] = 0;
   out_5059879879978958987[264] = 0;
   out_5059879879978958987[265] = 0;
   out_5059879879978958987[266] = 1;
   out_5059879879978958987[267] = 0;
   out_5059879879978958987[268] = 0;
   out_5059879879978958987[269] = 0;
   out_5059879879978958987[270] = 0;
   out_5059879879978958987[271] = 0;
   out_5059879879978958987[272] = 0;
   out_5059879879978958987[273] = 0;
   out_5059879879978958987[274] = 0;
   out_5059879879978958987[275] = 0;
   out_5059879879978958987[276] = 0;
   out_5059879879978958987[277] = 0;
   out_5059879879978958987[278] = 0;
   out_5059879879978958987[279] = 0;
   out_5059879879978958987[280] = 0;
   out_5059879879978958987[281] = 0;
   out_5059879879978958987[282] = 0;
   out_5059879879978958987[283] = 0;
   out_5059879879978958987[284] = 0;
   out_5059879879978958987[285] = 1;
   out_5059879879978958987[286] = 0;
   out_5059879879978958987[287] = 0;
   out_5059879879978958987[288] = 0;
   out_5059879879978958987[289] = 0;
   out_5059879879978958987[290] = 0;
   out_5059879879978958987[291] = 0;
   out_5059879879978958987[292] = 0;
   out_5059879879978958987[293] = 0;
   out_5059879879978958987[294] = 0;
   out_5059879879978958987[295] = 0;
   out_5059879879978958987[296] = 0;
   out_5059879879978958987[297] = 0;
   out_5059879879978958987[298] = 0;
   out_5059879879978958987[299] = 0;
   out_5059879879978958987[300] = 0;
   out_5059879879978958987[301] = 0;
   out_5059879879978958987[302] = 0;
   out_5059879879978958987[303] = 0;
   out_5059879879978958987[304] = 1;
   out_5059879879978958987[305] = 0;
   out_5059879879978958987[306] = 0;
   out_5059879879978958987[307] = 0;
   out_5059879879978958987[308] = 0;
   out_5059879879978958987[309] = 0;
   out_5059879879978958987[310] = 0;
   out_5059879879978958987[311] = 0;
   out_5059879879978958987[312] = 0;
   out_5059879879978958987[313] = 0;
   out_5059879879978958987[314] = 0;
   out_5059879879978958987[315] = 0;
   out_5059879879978958987[316] = 0;
   out_5059879879978958987[317] = 0;
   out_5059879879978958987[318] = 0;
   out_5059879879978958987[319] = 0;
   out_5059879879978958987[320] = 0;
   out_5059879879978958987[321] = 0;
   out_5059879879978958987[322] = 0;
   out_5059879879978958987[323] = 1;
}
void h_4(double *state, double *unused, double *out_8306941651867152905) {
   out_8306941651867152905[0] = state[6] + state[9];
   out_8306941651867152905[1] = state[7] + state[10];
   out_8306941651867152905[2] = state[8] + state[11];
}
void H_4(double *state, double *unused, double *out_41214293034027689) {
   out_41214293034027689[0] = 0;
   out_41214293034027689[1] = 0;
   out_41214293034027689[2] = 0;
   out_41214293034027689[3] = 0;
   out_41214293034027689[4] = 0;
   out_41214293034027689[5] = 0;
   out_41214293034027689[6] = 1;
   out_41214293034027689[7] = 0;
   out_41214293034027689[8] = 0;
   out_41214293034027689[9] = 1;
   out_41214293034027689[10] = 0;
   out_41214293034027689[11] = 0;
   out_41214293034027689[12] = 0;
   out_41214293034027689[13] = 0;
   out_41214293034027689[14] = 0;
   out_41214293034027689[15] = 0;
   out_41214293034027689[16] = 0;
   out_41214293034027689[17] = 0;
   out_41214293034027689[18] = 0;
   out_41214293034027689[19] = 0;
   out_41214293034027689[20] = 0;
   out_41214293034027689[21] = 0;
   out_41214293034027689[22] = 0;
   out_41214293034027689[23] = 0;
   out_41214293034027689[24] = 0;
   out_41214293034027689[25] = 1;
   out_41214293034027689[26] = 0;
   out_41214293034027689[27] = 0;
   out_41214293034027689[28] = 1;
   out_41214293034027689[29] = 0;
   out_41214293034027689[30] = 0;
   out_41214293034027689[31] = 0;
   out_41214293034027689[32] = 0;
   out_41214293034027689[33] = 0;
   out_41214293034027689[34] = 0;
   out_41214293034027689[35] = 0;
   out_41214293034027689[36] = 0;
   out_41214293034027689[37] = 0;
   out_41214293034027689[38] = 0;
   out_41214293034027689[39] = 0;
   out_41214293034027689[40] = 0;
   out_41214293034027689[41] = 0;
   out_41214293034027689[42] = 0;
   out_41214293034027689[43] = 0;
   out_41214293034027689[44] = 1;
   out_41214293034027689[45] = 0;
   out_41214293034027689[46] = 0;
   out_41214293034027689[47] = 1;
   out_41214293034027689[48] = 0;
   out_41214293034027689[49] = 0;
   out_41214293034027689[50] = 0;
   out_41214293034027689[51] = 0;
   out_41214293034027689[52] = 0;
   out_41214293034027689[53] = 0;
}
void h_10(double *state, double *unused, double *out_4127406154043783729) {
   out_4127406154043783729[0] = 9.8100000000000005*sin(state[1]) - state[4]*state[8] + state[5]*state[7] + state[12] + state[15];
   out_4127406154043783729[1] = -9.8100000000000005*sin(state[0])*cos(state[1]) + state[3]*state[8] - state[5]*state[6] + state[13] + state[16];
   out_4127406154043783729[2] = -9.8100000000000005*cos(state[0])*cos(state[1]) - state[3]*state[7] + state[4]*state[6] + state[14] + state[17];
}
void H_10(double *state, double *unused, double *out_7440694459650338948) {
   out_7440694459650338948[0] = 0;
   out_7440694459650338948[1] = 9.8100000000000005*cos(state[1]);
   out_7440694459650338948[2] = 0;
   out_7440694459650338948[3] = 0;
   out_7440694459650338948[4] = -state[8];
   out_7440694459650338948[5] = state[7];
   out_7440694459650338948[6] = 0;
   out_7440694459650338948[7] = state[5];
   out_7440694459650338948[8] = -state[4];
   out_7440694459650338948[9] = 0;
   out_7440694459650338948[10] = 0;
   out_7440694459650338948[11] = 0;
   out_7440694459650338948[12] = 1;
   out_7440694459650338948[13] = 0;
   out_7440694459650338948[14] = 0;
   out_7440694459650338948[15] = 1;
   out_7440694459650338948[16] = 0;
   out_7440694459650338948[17] = 0;
   out_7440694459650338948[18] = -9.8100000000000005*cos(state[0])*cos(state[1]);
   out_7440694459650338948[19] = 9.8100000000000005*sin(state[0])*sin(state[1]);
   out_7440694459650338948[20] = 0;
   out_7440694459650338948[21] = state[8];
   out_7440694459650338948[22] = 0;
   out_7440694459650338948[23] = -state[6];
   out_7440694459650338948[24] = -state[5];
   out_7440694459650338948[25] = 0;
   out_7440694459650338948[26] = state[3];
   out_7440694459650338948[27] = 0;
   out_7440694459650338948[28] = 0;
   out_7440694459650338948[29] = 0;
   out_7440694459650338948[30] = 0;
   out_7440694459650338948[31] = 1;
   out_7440694459650338948[32] = 0;
   out_7440694459650338948[33] = 0;
   out_7440694459650338948[34] = 1;
   out_7440694459650338948[35] = 0;
   out_7440694459650338948[36] = 9.8100000000000005*sin(state[0])*cos(state[1]);
   out_7440694459650338948[37] = 9.8100000000000005*sin(state[1])*cos(state[0]);
   out_7440694459650338948[38] = 0;
   out_7440694459650338948[39] = -state[7];
   out_7440694459650338948[40] = state[6];
   out_7440694459650338948[41] = 0;
   out_7440694459650338948[42] = state[4];
   out_7440694459650338948[43] = -state[3];
   out_7440694459650338948[44] = 0;
   out_7440694459650338948[45] = 0;
   out_7440694459650338948[46] = 0;
   out_7440694459650338948[47] = 0;
   out_7440694459650338948[48] = 0;
   out_7440694459650338948[49] = 0;
   out_7440694459650338948[50] = 1;
   out_7440694459650338948[51] = 0;
   out_7440694459650338948[52] = 0;
   out_7440694459650338948[53] = 1;
}
void h_13(double *state, double *unused, double *out_2904306600296367164) {
   out_2904306600296367164[0] = state[3];
   out_2904306600296367164[1] = state[4];
   out_2904306600296367164[2] = state[5];
}
void H_13(double *state, double *unused, double *out_3171059532298305112) {
   out_3171059532298305112[0] = 0;
   out_3171059532298305112[1] = 0;
   out_3171059532298305112[2] = 0;
   out_3171059532298305112[3] = 1;
   out_3171059532298305112[4] = 0;
   out_3171059532298305112[5] = 0;
   out_3171059532298305112[6] = 0;
   out_3171059532298305112[7] = 0;
   out_3171059532298305112[8] = 0;
   out_3171059532298305112[9] = 0;
   out_3171059532298305112[10] = 0;
   out_3171059532298305112[11] = 0;
   out_3171059532298305112[12] = 0;
   out_3171059532298305112[13] = 0;
   out_3171059532298305112[14] = 0;
   out_3171059532298305112[15] = 0;
   out_3171059532298305112[16] = 0;
   out_3171059532298305112[17] = 0;
   out_3171059532298305112[18] = 0;
   out_3171059532298305112[19] = 0;
   out_3171059532298305112[20] = 0;
   out_3171059532298305112[21] = 0;
   out_3171059532298305112[22] = 1;
   out_3171059532298305112[23] = 0;
   out_3171059532298305112[24] = 0;
   out_3171059532298305112[25] = 0;
   out_3171059532298305112[26] = 0;
   out_3171059532298305112[27] = 0;
   out_3171059532298305112[28] = 0;
   out_3171059532298305112[29] = 0;
   out_3171059532298305112[30] = 0;
   out_3171059532298305112[31] = 0;
   out_3171059532298305112[32] = 0;
   out_3171059532298305112[33] = 0;
   out_3171059532298305112[34] = 0;
   out_3171059532298305112[35] = 0;
   out_3171059532298305112[36] = 0;
   out_3171059532298305112[37] = 0;
   out_3171059532298305112[38] = 0;
   out_3171059532298305112[39] = 0;
   out_3171059532298305112[40] = 0;
   out_3171059532298305112[41] = 1;
   out_3171059532298305112[42] = 0;
   out_3171059532298305112[43] = 0;
   out_3171059532298305112[44] = 0;
   out_3171059532298305112[45] = 0;
   out_3171059532298305112[46] = 0;
   out_3171059532298305112[47] = 0;
   out_3171059532298305112[48] = 0;
   out_3171059532298305112[49] = 0;
   out_3171059532298305112[50] = 0;
   out_3171059532298305112[51] = 0;
   out_3171059532298305112[52] = 0;
   out_3171059532298305112[53] = 0;
}
void h_14(double *state, double *unused, double *out_6520989930447577741) {
   out_6520989930447577741[0] = state[6];
   out_6520989930447577741[1] = state[7];
   out_6520989930447577741[2] = state[8];
}
void H_14(double *state, double *unused, double *out_3922026563305456840) {
   out_3922026563305456840[0] = 0;
   out_3922026563305456840[1] = 0;
   out_3922026563305456840[2] = 0;
   out_3922026563305456840[3] = 0;
   out_3922026563305456840[4] = 0;
   out_3922026563305456840[5] = 0;
   out_3922026563305456840[6] = 1;
   out_3922026563305456840[7] = 0;
   out_3922026563305456840[8] = 0;
   out_3922026563305456840[9] = 0;
   out_3922026563305456840[10] = 0;
   out_3922026563305456840[11] = 0;
   out_3922026563305456840[12] = 0;
   out_3922026563305456840[13] = 0;
   out_3922026563305456840[14] = 0;
   out_3922026563305456840[15] = 0;
   out_3922026563305456840[16] = 0;
   out_3922026563305456840[17] = 0;
   out_3922026563305456840[18] = 0;
   out_3922026563305456840[19] = 0;
   out_3922026563305456840[20] = 0;
   out_3922026563305456840[21] = 0;
   out_3922026563305456840[22] = 0;
   out_3922026563305456840[23] = 0;
   out_3922026563305456840[24] = 0;
   out_3922026563305456840[25] = 1;
   out_3922026563305456840[26] = 0;
   out_3922026563305456840[27] = 0;
   out_3922026563305456840[28] = 0;
   out_3922026563305456840[29] = 0;
   out_3922026563305456840[30] = 0;
   out_3922026563305456840[31] = 0;
   out_3922026563305456840[32] = 0;
   out_3922026563305456840[33] = 0;
   out_3922026563305456840[34] = 0;
   out_3922026563305456840[35] = 0;
   out_3922026563305456840[36] = 0;
   out_3922026563305456840[37] = 0;
   out_3922026563305456840[38] = 0;
   out_3922026563305456840[39] = 0;
   out_3922026563305456840[40] = 0;
   out_3922026563305456840[41] = 0;
   out_3922026563305456840[42] = 0;
   out_3922026563305456840[43] = 0;
   out_3922026563305456840[44] = 1;
   out_3922026563305456840[45] = 0;
   out_3922026563305456840[46] = 0;
   out_3922026563305456840[47] = 0;
   out_3922026563305456840[48] = 0;
   out_3922026563305456840[49] = 0;
   out_3922026563305456840[50] = 0;
   out_3922026563305456840[51] = 0;
   out_3922026563305456840[52] = 0;
   out_3922026563305456840[53] = 0;
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
void pose_err_fun(double *nom_x, double *delta_x, double *out_8942860553158829980) {
  err_fun(nom_x, delta_x, out_8942860553158829980);
}
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_2254641408306223903) {
  inv_err_fun(nom_x, true_x, out_2254641408306223903);
}
void pose_H_mod_fun(double *state, double *out_76292315910108330) {
  H_mod_fun(state, out_76292315910108330);
}
void pose_f_fun(double *state, double dt, double *out_1412378285720339068) {
  f_fun(state,  dt, out_1412378285720339068);
}
void pose_F_fun(double *state, double dt, double *out_5059879879978958987) {
  F_fun(state,  dt, out_5059879879978958987);
}
void pose_h_4(double *state, double *unused, double *out_8306941651867152905) {
  h_4(state, unused, out_8306941651867152905);
}
void pose_H_4(double *state, double *unused, double *out_41214293034027689) {
  H_4(state, unused, out_41214293034027689);
}
void pose_h_10(double *state, double *unused, double *out_4127406154043783729) {
  h_10(state, unused, out_4127406154043783729);
}
void pose_H_10(double *state, double *unused, double *out_7440694459650338948) {
  H_10(state, unused, out_7440694459650338948);
}
void pose_h_13(double *state, double *unused, double *out_2904306600296367164) {
  h_13(state, unused, out_2904306600296367164);
}
void pose_H_13(double *state, double *unused, double *out_3171059532298305112) {
  H_13(state, unused, out_3171059532298305112);
}
void pose_h_14(double *state, double *unused, double *out_6520989930447577741) {
  h_14(state, unused, out_6520989930447577741);
}
void pose_H_14(double *state, double *unused, double *out_3922026563305456840) {
  H_14(state, unused, out_3922026563305456840);
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
