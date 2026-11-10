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
void err_fun(double *nom_x, double *delta_x, double *out_944171533359057280) {
   out_944171533359057280[0] = delta_x[0] + nom_x[0];
   out_944171533359057280[1] = delta_x[1] + nom_x[1];
   out_944171533359057280[2] = delta_x[2] + nom_x[2];
   out_944171533359057280[3] = delta_x[3] + nom_x[3];
   out_944171533359057280[4] = delta_x[4] + nom_x[4];
   out_944171533359057280[5] = delta_x[5] + nom_x[5];
   out_944171533359057280[6] = delta_x[6] + nom_x[6];
   out_944171533359057280[7] = delta_x[7] + nom_x[7];
   out_944171533359057280[8] = delta_x[8] + nom_x[8];
   out_944171533359057280[9] = delta_x[9] + nom_x[9];
   out_944171533359057280[10] = delta_x[10] + nom_x[10];
   out_944171533359057280[11] = delta_x[11] + nom_x[11];
   out_944171533359057280[12] = delta_x[12] + nom_x[12];
   out_944171533359057280[13] = delta_x[13] + nom_x[13];
   out_944171533359057280[14] = delta_x[14] + nom_x[14];
   out_944171533359057280[15] = delta_x[15] + nom_x[15];
   out_944171533359057280[16] = delta_x[16] + nom_x[16];
   out_944171533359057280[17] = delta_x[17] + nom_x[17];
}
void inv_err_fun(double *nom_x, double *true_x, double *out_5987343215061063396) {
   out_5987343215061063396[0] = -nom_x[0] + true_x[0];
   out_5987343215061063396[1] = -nom_x[1] + true_x[1];
   out_5987343215061063396[2] = -nom_x[2] + true_x[2];
   out_5987343215061063396[3] = -nom_x[3] + true_x[3];
   out_5987343215061063396[4] = -nom_x[4] + true_x[4];
   out_5987343215061063396[5] = -nom_x[5] + true_x[5];
   out_5987343215061063396[6] = -nom_x[6] + true_x[6];
   out_5987343215061063396[7] = -nom_x[7] + true_x[7];
   out_5987343215061063396[8] = -nom_x[8] + true_x[8];
   out_5987343215061063396[9] = -nom_x[9] + true_x[9];
   out_5987343215061063396[10] = -nom_x[10] + true_x[10];
   out_5987343215061063396[11] = -nom_x[11] + true_x[11];
   out_5987343215061063396[12] = -nom_x[12] + true_x[12];
   out_5987343215061063396[13] = -nom_x[13] + true_x[13];
   out_5987343215061063396[14] = -nom_x[14] + true_x[14];
   out_5987343215061063396[15] = -nom_x[15] + true_x[15];
   out_5987343215061063396[16] = -nom_x[16] + true_x[16];
   out_5987343215061063396[17] = -nom_x[17] + true_x[17];
}
void H_mod_fun(double *state, double *out_922696574028504956) {
   out_922696574028504956[0] = 1.0;
   out_922696574028504956[1] = 0.0;
   out_922696574028504956[2] = 0.0;
   out_922696574028504956[3] = 0.0;
   out_922696574028504956[4] = 0.0;
   out_922696574028504956[5] = 0.0;
   out_922696574028504956[6] = 0.0;
   out_922696574028504956[7] = 0.0;
   out_922696574028504956[8] = 0.0;
   out_922696574028504956[9] = 0.0;
   out_922696574028504956[10] = 0.0;
   out_922696574028504956[11] = 0.0;
   out_922696574028504956[12] = 0.0;
   out_922696574028504956[13] = 0.0;
   out_922696574028504956[14] = 0.0;
   out_922696574028504956[15] = 0.0;
   out_922696574028504956[16] = 0.0;
   out_922696574028504956[17] = 0.0;
   out_922696574028504956[18] = 0.0;
   out_922696574028504956[19] = 1.0;
   out_922696574028504956[20] = 0.0;
   out_922696574028504956[21] = 0.0;
   out_922696574028504956[22] = 0.0;
   out_922696574028504956[23] = 0.0;
   out_922696574028504956[24] = 0.0;
   out_922696574028504956[25] = 0.0;
   out_922696574028504956[26] = 0.0;
   out_922696574028504956[27] = 0.0;
   out_922696574028504956[28] = 0.0;
   out_922696574028504956[29] = 0.0;
   out_922696574028504956[30] = 0.0;
   out_922696574028504956[31] = 0.0;
   out_922696574028504956[32] = 0.0;
   out_922696574028504956[33] = 0.0;
   out_922696574028504956[34] = 0.0;
   out_922696574028504956[35] = 0.0;
   out_922696574028504956[36] = 0.0;
   out_922696574028504956[37] = 0.0;
   out_922696574028504956[38] = 1.0;
   out_922696574028504956[39] = 0.0;
   out_922696574028504956[40] = 0.0;
   out_922696574028504956[41] = 0.0;
   out_922696574028504956[42] = 0.0;
   out_922696574028504956[43] = 0.0;
   out_922696574028504956[44] = 0.0;
   out_922696574028504956[45] = 0.0;
   out_922696574028504956[46] = 0.0;
   out_922696574028504956[47] = 0.0;
   out_922696574028504956[48] = 0.0;
   out_922696574028504956[49] = 0.0;
   out_922696574028504956[50] = 0.0;
   out_922696574028504956[51] = 0.0;
   out_922696574028504956[52] = 0.0;
   out_922696574028504956[53] = 0.0;
   out_922696574028504956[54] = 0.0;
   out_922696574028504956[55] = 0.0;
   out_922696574028504956[56] = 0.0;
   out_922696574028504956[57] = 1.0;
   out_922696574028504956[58] = 0.0;
   out_922696574028504956[59] = 0.0;
   out_922696574028504956[60] = 0.0;
   out_922696574028504956[61] = 0.0;
   out_922696574028504956[62] = 0.0;
   out_922696574028504956[63] = 0.0;
   out_922696574028504956[64] = 0.0;
   out_922696574028504956[65] = 0.0;
   out_922696574028504956[66] = 0.0;
   out_922696574028504956[67] = 0.0;
   out_922696574028504956[68] = 0.0;
   out_922696574028504956[69] = 0.0;
   out_922696574028504956[70] = 0.0;
   out_922696574028504956[71] = 0.0;
   out_922696574028504956[72] = 0.0;
   out_922696574028504956[73] = 0.0;
   out_922696574028504956[74] = 0.0;
   out_922696574028504956[75] = 0.0;
   out_922696574028504956[76] = 1.0;
   out_922696574028504956[77] = 0.0;
   out_922696574028504956[78] = 0.0;
   out_922696574028504956[79] = 0.0;
   out_922696574028504956[80] = 0.0;
   out_922696574028504956[81] = 0.0;
   out_922696574028504956[82] = 0.0;
   out_922696574028504956[83] = 0.0;
   out_922696574028504956[84] = 0.0;
   out_922696574028504956[85] = 0.0;
   out_922696574028504956[86] = 0.0;
   out_922696574028504956[87] = 0.0;
   out_922696574028504956[88] = 0.0;
   out_922696574028504956[89] = 0.0;
   out_922696574028504956[90] = 0.0;
   out_922696574028504956[91] = 0.0;
   out_922696574028504956[92] = 0.0;
   out_922696574028504956[93] = 0.0;
   out_922696574028504956[94] = 0.0;
   out_922696574028504956[95] = 1.0;
   out_922696574028504956[96] = 0.0;
   out_922696574028504956[97] = 0.0;
   out_922696574028504956[98] = 0.0;
   out_922696574028504956[99] = 0.0;
   out_922696574028504956[100] = 0.0;
   out_922696574028504956[101] = 0.0;
   out_922696574028504956[102] = 0.0;
   out_922696574028504956[103] = 0.0;
   out_922696574028504956[104] = 0.0;
   out_922696574028504956[105] = 0.0;
   out_922696574028504956[106] = 0.0;
   out_922696574028504956[107] = 0.0;
   out_922696574028504956[108] = 0.0;
   out_922696574028504956[109] = 0.0;
   out_922696574028504956[110] = 0.0;
   out_922696574028504956[111] = 0.0;
   out_922696574028504956[112] = 0.0;
   out_922696574028504956[113] = 0.0;
   out_922696574028504956[114] = 1.0;
   out_922696574028504956[115] = 0.0;
   out_922696574028504956[116] = 0.0;
   out_922696574028504956[117] = 0.0;
   out_922696574028504956[118] = 0.0;
   out_922696574028504956[119] = 0.0;
   out_922696574028504956[120] = 0.0;
   out_922696574028504956[121] = 0.0;
   out_922696574028504956[122] = 0.0;
   out_922696574028504956[123] = 0.0;
   out_922696574028504956[124] = 0.0;
   out_922696574028504956[125] = 0.0;
   out_922696574028504956[126] = 0.0;
   out_922696574028504956[127] = 0.0;
   out_922696574028504956[128] = 0.0;
   out_922696574028504956[129] = 0.0;
   out_922696574028504956[130] = 0.0;
   out_922696574028504956[131] = 0.0;
   out_922696574028504956[132] = 0.0;
   out_922696574028504956[133] = 1.0;
   out_922696574028504956[134] = 0.0;
   out_922696574028504956[135] = 0.0;
   out_922696574028504956[136] = 0.0;
   out_922696574028504956[137] = 0.0;
   out_922696574028504956[138] = 0.0;
   out_922696574028504956[139] = 0.0;
   out_922696574028504956[140] = 0.0;
   out_922696574028504956[141] = 0.0;
   out_922696574028504956[142] = 0.0;
   out_922696574028504956[143] = 0.0;
   out_922696574028504956[144] = 0.0;
   out_922696574028504956[145] = 0.0;
   out_922696574028504956[146] = 0.0;
   out_922696574028504956[147] = 0.0;
   out_922696574028504956[148] = 0.0;
   out_922696574028504956[149] = 0.0;
   out_922696574028504956[150] = 0.0;
   out_922696574028504956[151] = 0.0;
   out_922696574028504956[152] = 1.0;
   out_922696574028504956[153] = 0.0;
   out_922696574028504956[154] = 0.0;
   out_922696574028504956[155] = 0.0;
   out_922696574028504956[156] = 0.0;
   out_922696574028504956[157] = 0.0;
   out_922696574028504956[158] = 0.0;
   out_922696574028504956[159] = 0.0;
   out_922696574028504956[160] = 0.0;
   out_922696574028504956[161] = 0.0;
   out_922696574028504956[162] = 0.0;
   out_922696574028504956[163] = 0.0;
   out_922696574028504956[164] = 0.0;
   out_922696574028504956[165] = 0.0;
   out_922696574028504956[166] = 0.0;
   out_922696574028504956[167] = 0.0;
   out_922696574028504956[168] = 0.0;
   out_922696574028504956[169] = 0.0;
   out_922696574028504956[170] = 0.0;
   out_922696574028504956[171] = 1.0;
   out_922696574028504956[172] = 0.0;
   out_922696574028504956[173] = 0.0;
   out_922696574028504956[174] = 0.0;
   out_922696574028504956[175] = 0.0;
   out_922696574028504956[176] = 0.0;
   out_922696574028504956[177] = 0.0;
   out_922696574028504956[178] = 0.0;
   out_922696574028504956[179] = 0.0;
   out_922696574028504956[180] = 0.0;
   out_922696574028504956[181] = 0.0;
   out_922696574028504956[182] = 0.0;
   out_922696574028504956[183] = 0.0;
   out_922696574028504956[184] = 0.0;
   out_922696574028504956[185] = 0.0;
   out_922696574028504956[186] = 0.0;
   out_922696574028504956[187] = 0.0;
   out_922696574028504956[188] = 0.0;
   out_922696574028504956[189] = 0.0;
   out_922696574028504956[190] = 1.0;
   out_922696574028504956[191] = 0.0;
   out_922696574028504956[192] = 0.0;
   out_922696574028504956[193] = 0.0;
   out_922696574028504956[194] = 0.0;
   out_922696574028504956[195] = 0.0;
   out_922696574028504956[196] = 0.0;
   out_922696574028504956[197] = 0.0;
   out_922696574028504956[198] = 0.0;
   out_922696574028504956[199] = 0.0;
   out_922696574028504956[200] = 0.0;
   out_922696574028504956[201] = 0.0;
   out_922696574028504956[202] = 0.0;
   out_922696574028504956[203] = 0.0;
   out_922696574028504956[204] = 0.0;
   out_922696574028504956[205] = 0.0;
   out_922696574028504956[206] = 0.0;
   out_922696574028504956[207] = 0.0;
   out_922696574028504956[208] = 0.0;
   out_922696574028504956[209] = 1.0;
   out_922696574028504956[210] = 0.0;
   out_922696574028504956[211] = 0.0;
   out_922696574028504956[212] = 0.0;
   out_922696574028504956[213] = 0.0;
   out_922696574028504956[214] = 0.0;
   out_922696574028504956[215] = 0.0;
   out_922696574028504956[216] = 0.0;
   out_922696574028504956[217] = 0.0;
   out_922696574028504956[218] = 0.0;
   out_922696574028504956[219] = 0.0;
   out_922696574028504956[220] = 0.0;
   out_922696574028504956[221] = 0.0;
   out_922696574028504956[222] = 0.0;
   out_922696574028504956[223] = 0.0;
   out_922696574028504956[224] = 0.0;
   out_922696574028504956[225] = 0.0;
   out_922696574028504956[226] = 0.0;
   out_922696574028504956[227] = 0.0;
   out_922696574028504956[228] = 1.0;
   out_922696574028504956[229] = 0.0;
   out_922696574028504956[230] = 0.0;
   out_922696574028504956[231] = 0.0;
   out_922696574028504956[232] = 0.0;
   out_922696574028504956[233] = 0.0;
   out_922696574028504956[234] = 0.0;
   out_922696574028504956[235] = 0.0;
   out_922696574028504956[236] = 0.0;
   out_922696574028504956[237] = 0.0;
   out_922696574028504956[238] = 0.0;
   out_922696574028504956[239] = 0.0;
   out_922696574028504956[240] = 0.0;
   out_922696574028504956[241] = 0.0;
   out_922696574028504956[242] = 0.0;
   out_922696574028504956[243] = 0.0;
   out_922696574028504956[244] = 0.0;
   out_922696574028504956[245] = 0.0;
   out_922696574028504956[246] = 0.0;
   out_922696574028504956[247] = 1.0;
   out_922696574028504956[248] = 0.0;
   out_922696574028504956[249] = 0.0;
   out_922696574028504956[250] = 0.0;
   out_922696574028504956[251] = 0.0;
   out_922696574028504956[252] = 0.0;
   out_922696574028504956[253] = 0.0;
   out_922696574028504956[254] = 0.0;
   out_922696574028504956[255] = 0.0;
   out_922696574028504956[256] = 0.0;
   out_922696574028504956[257] = 0.0;
   out_922696574028504956[258] = 0.0;
   out_922696574028504956[259] = 0.0;
   out_922696574028504956[260] = 0.0;
   out_922696574028504956[261] = 0.0;
   out_922696574028504956[262] = 0.0;
   out_922696574028504956[263] = 0.0;
   out_922696574028504956[264] = 0.0;
   out_922696574028504956[265] = 0.0;
   out_922696574028504956[266] = 1.0;
   out_922696574028504956[267] = 0.0;
   out_922696574028504956[268] = 0.0;
   out_922696574028504956[269] = 0.0;
   out_922696574028504956[270] = 0.0;
   out_922696574028504956[271] = 0.0;
   out_922696574028504956[272] = 0.0;
   out_922696574028504956[273] = 0.0;
   out_922696574028504956[274] = 0.0;
   out_922696574028504956[275] = 0.0;
   out_922696574028504956[276] = 0.0;
   out_922696574028504956[277] = 0.0;
   out_922696574028504956[278] = 0.0;
   out_922696574028504956[279] = 0.0;
   out_922696574028504956[280] = 0.0;
   out_922696574028504956[281] = 0.0;
   out_922696574028504956[282] = 0.0;
   out_922696574028504956[283] = 0.0;
   out_922696574028504956[284] = 0.0;
   out_922696574028504956[285] = 1.0;
   out_922696574028504956[286] = 0.0;
   out_922696574028504956[287] = 0.0;
   out_922696574028504956[288] = 0.0;
   out_922696574028504956[289] = 0.0;
   out_922696574028504956[290] = 0.0;
   out_922696574028504956[291] = 0.0;
   out_922696574028504956[292] = 0.0;
   out_922696574028504956[293] = 0.0;
   out_922696574028504956[294] = 0.0;
   out_922696574028504956[295] = 0.0;
   out_922696574028504956[296] = 0.0;
   out_922696574028504956[297] = 0.0;
   out_922696574028504956[298] = 0.0;
   out_922696574028504956[299] = 0.0;
   out_922696574028504956[300] = 0.0;
   out_922696574028504956[301] = 0.0;
   out_922696574028504956[302] = 0.0;
   out_922696574028504956[303] = 0.0;
   out_922696574028504956[304] = 1.0;
   out_922696574028504956[305] = 0.0;
   out_922696574028504956[306] = 0.0;
   out_922696574028504956[307] = 0.0;
   out_922696574028504956[308] = 0.0;
   out_922696574028504956[309] = 0.0;
   out_922696574028504956[310] = 0.0;
   out_922696574028504956[311] = 0.0;
   out_922696574028504956[312] = 0.0;
   out_922696574028504956[313] = 0.0;
   out_922696574028504956[314] = 0.0;
   out_922696574028504956[315] = 0.0;
   out_922696574028504956[316] = 0.0;
   out_922696574028504956[317] = 0.0;
   out_922696574028504956[318] = 0.0;
   out_922696574028504956[319] = 0.0;
   out_922696574028504956[320] = 0.0;
   out_922696574028504956[321] = 0.0;
   out_922696574028504956[322] = 0.0;
   out_922696574028504956[323] = 1.0;
}
void f_fun(double *state, double dt, double *out_7271275588779717019) {
   out_7271275588779717019[0] = atan2((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), -(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]));
   out_7271275588779717019[1] = asin(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]));
   out_7271275588779717019[2] = atan2(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), -(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]));
   out_7271275588779717019[3] = dt*state[12] + state[3];
   out_7271275588779717019[4] = dt*state[13] + state[4];
   out_7271275588779717019[5] = dt*state[14] + state[5];
   out_7271275588779717019[6] = state[6];
   out_7271275588779717019[7] = state[7];
   out_7271275588779717019[8] = state[8];
   out_7271275588779717019[9] = state[9];
   out_7271275588779717019[10] = state[10];
   out_7271275588779717019[11] = state[11];
   out_7271275588779717019[12] = state[12];
   out_7271275588779717019[13] = state[13];
   out_7271275588779717019[14] = state[14];
   out_7271275588779717019[15] = state[15];
   out_7271275588779717019[16] = state[16];
   out_7271275588779717019[17] = state[17];
}
void F_fun(double *state, double dt, double *out_1488608338000593967) {
   out_1488608338000593967[0] = ((-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*cos(state[0])*cos(state[1]) - sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*cos(state[0])*cos(state[1]) - sin(dt*state[6])*sin(state[0])*cos(dt*state[7])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_1488608338000593967[1] = ((-sin(dt*state[6])*sin(dt*state[8]) - sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*cos(state[1]) - (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*sin(state[1]) - sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(state[0]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*sin(state[1]) + (-sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) + sin(dt*state[8])*cos(dt*state[6]))*cos(state[1]) - sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(state[0]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_1488608338000593967[2] = 0;
   out_1488608338000593967[3] = 0;
   out_1488608338000593967[4] = 0;
   out_1488608338000593967[5] = 0;
   out_1488608338000593967[6] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(dt*cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) - dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_1488608338000593967[7] = (-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[6])*sin(dt*state[7])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[6])*sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) - dt*sin(dt*state[6])*sin(state[1])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + (-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))*(-dt*sin(dt*state[7])*cos(dt*state[6])*cos(state[0])*cos(state[1]) + dt*sin(dt*state[8])*sin(state[0])*cos(dt*state[6])*cos(dt*state[7])*cos(state[1]) - dt*sin(state[1])*cos(dt*state[6])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_1488608338000593967[8] = ((dt*sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + dt*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (dt*sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]))*(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2)) + ((dt*sin(dt*state[6])*sin(dt*state[8]) + dt*sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (-dt*sin(dt*state[6])*cos(dt*state[8]) + dt*sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]))*(-(sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) + (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) - sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/(pow(-(sin(dt*state[6])*sin(dt*state[8]) + sin(dt*state[7])*cos(dt*state[6])*cos(dt*state[8]))*sin(state[1]) + (-sin(dt*state[6])*cos(dt*state[8]) + sin(dt*state[7])*sin(dt*state[8])*cos(dt*state[6]))*sin(state[0])*cos(state[1]) + cos(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2) + pow((sin(dt*state[6])*sin(dt*state[7])*sin(dt*state[8]) + cos(dt*state[6])*cos(dt*state[8]))*sin(state[0])*cos(state[1]) - (sin(dt*state[6])*sin(dt*state[7])*cos(dt*state[8]) - sin(dt*state[8])*cos(dt*state[6]))*sin(state[1]) + sin(dt*state[6])*cos(dt*state[7])*cos(state[0])*cos(state[1]), 2));
   out_1488608338000593967[9] = 0;
   out_1488608338000593967[10] = 0;
   out_1488608338000593967[11] = 0;
   out_1488608338000593967[12] = 0;
   out_1488608338000593967[13] = 0;
   out_1488608338000593967[14] = 0;
   out_1488608338000593967[15] = 0;
   out_1488608338000593967[16] = 0;
   out_1488608338000593967[17] = 0;
   out_1488608338000593967[18] = (-sin(dt*state[7])*sin(state[0])*cos(state[1]) - sin(dt*state[8])*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_1488608338000593967[19] = (-sin(dt*state[7])*sin(state[1])*cos(state[0]) + sin(dt*state[8])*sin(state[0])*sin(state[1])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_1488608338000593967[20] = 0;
   out_1488608338000593967[21] = 0;
   out_1488608338000593967[22] = 0;
   out_1488608338000593967[23] = 0;
   out_1488608338000593967[24] = 0;
   out_1488608338000593967[25] = (dt*sin(dt*state[7])*sin(dt*state[8])*sin(state[0])*cos(state[1]) - dt*sin(dt*state[7])*sin(state[1])*cos(dt*state[8]) + dt*cos(dt*state[7])*cos(state[0])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_1488608338000593967[26] = (-dt*sin(dt*state[8])*sin(state[1])*cos(dt*state[7]) - dt*sin(state[0])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/sqrt(1 - pow(sin(dt*state[7])*cos(state[0])*cos(state[1]) - sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1]) + sin(state[1])*cos(dt*state[7])*cos(dt*state[8]), 2));
   out_1488608338000593967[27] = 0;
   out_1488608338000593967[28] = 0;
   out_1488608338000593967[29] = 0;
   out_1488608338000593967[30] = 0;
   out_1488608338000593967[31] = 0;
   out_1488608338000593967[32] = 0;
   out_1488608338000593967[33] = 0;
   out_1488608338000593967[34] = 0;
   out_1488608338000593967[35] = 0;
   out_1488608338000593967[36] = ((sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_1488608338000593967[37] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-sin(dt*state[7])*sin(state[2])*cos(state[0])*cos(state[1]) + sin(dt*state[8])*sin(state[0])*sin(state[2])*cos(dt*state[7])*cos(state[1]) - sin(state[1])*sin(state[2])*cos(dt*state[7])*cos(dt*state[8]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(-sin(dt*state[7])*cos(state[0])*cos(state[1])*cos(state[2]) + sin(dt*state[8])*sin(state[0])*cos(dt*state[7])*cos(state[1])*cos(state[2]) - sin(state[1])*cos(dt*state[7])*cos(dt*state[8])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_1488608338000593967[38] = ((-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (-sin(state[0])*sin(state[1])*sin(state[2]) - cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_1488608338000593967[39] = 0;
   out_1488608338000593967[40] = 0;
   out_1488608338000593967[41] = 0;
   out_1488608338000593967[42] = 0;
   out_1488608338000593967[43] = (-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))*(dt*(sin(state[0])*cos(state[2]) - sin(state[1])*sin(state[2])*cos(state[0]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*sin(state[2])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + ((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))*(dt*(-sin(state[0])*sin(state[2]) - sin(state[1])*cos(state[0])*cos(state[2]))*cos(dt*state[7]) - dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[7])*sin(dt*state[8]) - dt*sin(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_1488608338000593967[44] = (dt*(sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*sin(state[2])*cos(dt*state[7])*cos(state[1]))*(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2)) + (dt*(sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*cos(dt*state[7])*cos(dt*state[8]) - dt*sin(dt*state[8])*cos(dt*state[7])*cos(state[1])*cos(state[2]))*((-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) - (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) - sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]))/(pow(-(sin(state[0])*sin(state[2]) + sin(state[1])*cos(state[0])*cos(state[2]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*cos(state[2]) - sin(state[2])*cos(state[0]))*sin(dt*state[8])*cos(dt*state[7]) + cos(dt*state[7])*cos(dt*state[8])*cos(state[1])*cos(state[2]), 2) + pow(-(-sin(state[0])*cos(state[2]) + sin(state[1])*sin(state[2])*cos(state[0]))*sin(dt*state[7]) + (sin(state[0])*sin(state[1])*sin(state[2]) + cos(state[0])*cos(state[2]))*sin(dt*state[8])*cos(dt*state[7]) + sin(state[2])*cos(dt*state[7])*cos(dt*state[8])*cos(state[1]), 2));
   out_1488608338000593967[45] = 0;
   out_1488608338000593967[46] = 0;
   out_1488608338000593967[47] = 0;
   out_1488608338000593967[48] = 0;
   out_1488608338000593967[49] = 0;
   out_1488608338000593967[50] = 0;
   out_1488608338000593967[51] = 0;
   out_1488608338000593967[52] = 0;
   out_1488608338000593967[53] = 0;
   out_1488608338000593967[54] = 0;
   out_1488608338000593967[55] = 0;
   out_1488608338000593967[56] = 0;
   out_1488608338000593967[57] = 1;
   out_1488608338000593967[58] = 0;
   out_1488608338000593967[59] = 0;
   out_1488608338000593967[60] = 0;
   out_1488608338000593967[61] = 0;
   out_1488608338000593967[62] = 0;
   out_1488608338000593967[63] = 0;
   out_1488608338000593967[64] = 0;
   out_1488608338000593967[65] = 0;
   out_1488608338000593967[66] = dt;
   out_1488608338000593967[67] = 0;
   out_1488608338000593967[68] = 0;
   out_1488608338000593967[69] = 0;
   out_1488608338000593967[70] = 0;
   out_1488608338000593967[71] = 0;
   out_1488608338000593967[72] = 0;
   out_1488608338000593967[73] = 0;
   out_1488608338000593967[74] = 0;
   out_1488608338000593967[75] = 0;
   out_1488608338000593967[76] = 1;
   out_1488608338000593967[77] = 0;
   out_1488608338000593967[78] = 0;
   out_1488608338000593967[79] = 0;
   out_1488608338000593967[80] = 0;
   out_1488608338000593967[81] = 0;
   out_1488608338000593967[82] = 0;
   out_1488608338000593967[83] = 0;
   out_1488608338000593967[84] = 0;
   out_1488608338000593967[85] = dt;
   out_1488608338000593967[86] = 0;
   out_1488608338000593967[87] = 0;
   out_1488608338000593967[88] = 0;
   out_1488608338000593967[89] = 0;
   out_1488608338000593967[90] = 0;
   out_1488608338000593967[91] = 0;
   out_1488608338000593967[92] = 0;
   out_1488608338000593967[93] = 0;
   out_1488608338000593967[94] = 0;
   out_1488608338000593967[95] = 1;
   out_1488608338000593967[96] = 0;
   out_1488608338000593967[97] = 0;
   out_1488608338000593967[98] = 0;
   out_1488608338000593967[99] = 0;
   out_1488608338000593967[100] = 0;
   out_1488608338000593967[101] = 0;
   out_1488608338000593967[102] = 0;
   out_1488608338000593967[103] = 0;
   out_1488608338000593967[104] = dt;
   out_1488608338000593967[105] = 0;
   out_1488608338000593967[106] = 0;
   out_1488608338000593967[107] = 0;
   out_1488608338000593967[108] = 0;
   out_1488608338000593967[109] = 0;
   out_1488608338000593967[110] = 0;
   out_1488608338000593967[111] = 0;
   out_1488608338000593967[112] = 0;
   out_1488608338000593967[113] = 0;
   out_1488608338000593967[114] = 1;
   out_1488608338000593967[115] = 0;
   out_1488608338000593967[116] = 0;
   out_1488608338000593967[117] = 0;
   out_1488608338000593967[118] = 0;
   out_1488608338000593967[119] = 0;
   out_1488608338000593967[120] = 0;
   out_1488608338000593967[121] = 0;
   out_1488608338000593967[122] = 0;
   out_1488608338000593967[123] = 0;
   out_1488608338000593967[124] = 0;
   out_1488608338000593967[125] = 0;
   out_1488608338000593967[126] = 0;
   out_1488608338000593967[127] = 0;
   out_1488608338000593967[128] = 0;
   out_1488608338000593967[129] = 0;
   out_1488608338000593967[130] = 0;
   out_1488608338000593967[131] = 0;
   out_1488608338000593967[132] = 0;
   out_1488608338000593967[133] = 1;
   out_1488608338000593967[134] = 0;
   out_1488608338000593967[135] = 0;
   out_1488608338000593967[136] = 0;
   out_1488608338000593967[137] = 0;
   out_1488608338000593967[138] = 0;
   out_1488608338000593967[139] = 0;
   out_1488608338000593967[140] = 0;
   out_1488608338000593967[141] = 0;
   out_1488608338000593967[142] = 0;
   out_1488608338000593967[143] = 0;
   out_1488608338000593967[144] = 0;
   out_1488608338000593967[145] = 0;
   out_1488608338000593967[146] = 0;
   out_1488608338000593967[147] = 0;
   out_1488608338000593967[148] = 0;
   out_1488608338000593967[149] = 0;
   out_1488608338000593967[150] = 0;
   out_1488608338000593967[151] = 0;
   out_1488608338000593967[152] = 1;
   out_1488608338000593967[153] = 0;
   out_1488608338000593967[154] = 0;
   out_1488608338000593967[155] = 0;
   out_1488608338000593967[156] = 0;
   out_1488608338000593967[157] = 0;
   out_1488608338000593967[158] = 0;
   out_1488608338000593967[159] = 0;
   out_1488608338000593967[160] = 0;
   out_1488608338000593967[161] = 0;
   out_1488608338000593967[162] = 0;
   out_1488608338000593967[163] = 0;
   out_1488608338000593967[164] = 0;
   out_1488608338000593967[165] = 0;
   out_1488608338000593967[166] = 0;
   out_1488608338000593967[167] = 0;
   out_1488608338000593967[168] = 0;
   out_1488608338000593967[169] = 0;
   out_1488608338000593967[170] = 0;
   out_1488608338000593967[171] = 1;
   out_1488608338000593967[172] = 0;
   out_1488608338000593967[173] = 0;
   out_1488608338000593967[174] = 0;
   out_1488608338000593967[175] = 0;
   out_1488608338000593967[176] = 0;
   out_1488608338000593967[177] = 0;
   out_1488608338000593967[178] = 0;
   out_1488608338000593967[179] = 0;
   out_1488608338000593967[180] = 0;
   out_1488608338000593967[181] = 0;
   out_1488608338000593967[182] = 0;
   out_1488608338000593967[183] = 0;
   out_1488608338000593967[184] = 0;
   out_1488608338000593967[185] = 0;
   out_1488608338000593967[186] = 0;
   out_1488608338000593967[187] = 0;
   out_1488608338000593967[188] = 0;
   out_1488608338000593967[189] = 0;
   out_1488608338000593967[190] = 1;
   out_1488608338000593967[191] = 0;
   out_1488608338000593967[192] = 0;
   out_1488608338000593967[193] = 0;
   out_1488608338000593967[194] = 0;
   out_1488608338000593967[195] = 0;
   out_1488608338000593967[196] = 0;
   out_1488608338000593967[197] = 0;
   out_1488608338000593967[198] = 0;
   out_1488608338000593967[199] = 0;
   out_1488608338000593967[200] = 0;
   out_1488608338000593967[201] = 0;
   out_1488608338000593967[202] = 0;
   out_1488608338000593967[203] = 0;
   out_1488608338000593967[204] = 0;
   out_1488608338000593967[205] = 0;
   out_1488608338000593967[206] = 0;
   out_1488608338000593967[207] = 0;
   out_1488608338000593967[208] = 0;
   out_1488608338000593967[209] = 1;
   out_1488608338000593967[210] = 0;
   out_1488608338000593967[211] = 0;
   out_1488608338000593967[212] = 0;
   out_1488608338000593967[213] = 0;
   out_1488608338000593967[214] = 0;
   out_1488608338000593967[215] = 0;
   out_1488608338000593967[216] = 0;
   out_1488608338000593967[217] = 0;
   out_1488608338000593967[218] = 0;
   out_1488608338000593967[219] = 0;
   out_1488608338000593967[220] = 0;
   out_1488608338000593967[221] = 0;
   out_1488608338000593967[222] = 0;
   out_1488608338000593967[223] = 0;
   out_1488608338000593967[224] = 0;
   out_1488608338000593967[225] = 0;
   out_1488608338000593967[226] = 0;
   out_1488608338000593967[227] = 0;
   out_1488608338000593967[228] = 1;
   out_1488608338000593967[229] = 0;
   out_1488608338000593967[230] = 0;
   out_1488608338000593967[231] = 0;
   out_1488608338000593967[232] = 0;
   out_1488608338000593967[233] = 0;
   out_1488608338000593967[234] = 0;
   out_1488608338000593967[235] = 0;
   out_1488608338000593967[236] = 0;
   out_1488608338000593967[237] = 0;
   out_1488608338000593967[238] = 0;
   out_1488608338000593967[239] = 0;
   out_1488608338000593967[240] = 0;
   out_1488608338000593967[241] = 0;
   out_1488608338000593967[242] = 0;
   out_1488608338000593967[243] = 0;
   out_1488608338000593967[244] = 0;
   out_1488608338000593967[245] = 0;
   out_1488608338000593967[246] = 0;
   out_1488608338000593967[247] = 1;
   out_1488608338000593967[248] = 0;
   out_1488608338000593967[249] = 0;
   out_1488608338000593967[250] = 0;
   out_1488608338000593967[251] = 0;
   out_1488608338000593967[252] = 0;
   out_1488608338000593967[253] = 0;
   out_1488608338000593967[254] = 0;
   out_1488608338000593967[255] = 0;
   out_1488608338000593967[256] = 0;
   out_1488608338000593967[257] = 0;
   out_1488608338000593967[258] = 0;
   out_1488608338000593967[259] = 0;
   out_1488608338000593967[260] = 0;
   out_1488608338000593967[261] = 0;
   out_1488608338000593967[262] = 0;
   out_1488608338000593967[263] = 0;
   out_1488608338000593967[264] = 0;
   out_1488608338000593967[265] = 0;
   out_1488608338000593967[266] = 1;
   out_1488608338000593967[267] = 0;
   out_1488608338000593967[268] = 0;
   out_1488608338000593967[269] = 0;
   out_1488608338000593967[270] = 0;
   out_1488608338000593967[271] = 0;
   out_1488608338000593967[272] = 0;
   out_1488608338000593967[273] = 0;
   out_1488608338000593967[274] = 0;
   out_1488608338000593967[275] = 0;
   out_1488608338000593967[276] = 0;
   out_1488608338000593967[277] = 0;
   out_1488608338000593967[278] = 0;
   out_1488608338000593967[279] = 0;
   out_1488608338000593967[280] = 0;
   out_1488608338000593967[281] = 0;
   out_1488608338000593967[282] = 0;
   out_1488608338000593967[283] = 0;
   out_1488608338000593967[284] = 0;
   out_1488608338000593967[285] = 1;
   out_1488608338000593967[286] = 0;
   out_1488608338000593967[287] = 0;
   out_1488608338000593967[288] = 0;
   out_1488608338000593967[289] = 0;
   out_1488608338000593967[290] = 0;
   out_1488608338000593967[291] = 0;
   out_1488608338000593967[292] = 0;
   out_1488608338000593967[293] = 0;
   out_1488608338000593967[294] = 0;
   out_1488608338000593967[295] = 0;
   out_1488608338000593967[296] = 0;
   out_1488608338000593967[297] = 0;
   out_1488608338000593967[298] = 0;
   out_1488608338000593967[299] = 0;
   out_1488608338000593967[300] = 0;
   out_1488608338000593967[301] = 0;
   out_1488608338000593967[302] = 0;
   out_1488608338000593967[303] = 0;
   out_1488608338000593967[304] = 1;
   out_1488608338000593967[305] = 0;
   out_1488608338000593967[306] = 0;
   out_1488608338000593967[307] = 0;
   out_1488608338000593967[308] = 0;
   out_1488608338000593967[309] = 0;
   out_1488608338000593967[310] = 0;
   out_1488608338000593967[311] = 0;
   out_1488608338000593967[312] = 0;
   out_1488608338000593967[313] = 0;
   out_1488608338000593967[314] = 0;
   out_1488608338000593967[315] = 0;
   out_1488608338000593967[316] = 0;
   out_1488608338000593967[317] = 0;
   out_1488608338000593967[318] = 0;
   out_1488608338000593967[319] = 0;
   out_1488608338000593967[320] = 0;
   out_1488608338000593967[321] = 0;
   out_1488608338000593967[322] = 0;
   out_1488608338000593967[323] = 1;
}
void h_4(double *state, double *unused, double *out_7790743839692054955) {
   out_7790743839692054955[0] = state[6] + state[9];
   out_7790743839692054955[1] = state[7] + state[10];
   out_7790743839692054955[2] = state[8] + state[11];
}
void H_4(double *state, double *unused, double *out_1533656544988368493) {
   out_1533656544988368493[0] = 0;
   out_1533656544988368493[1] = 0;
   out_1533656544988368493[2] = 0;
   out_1533656544988368493[3] = 0;
   out_1533656544988368493[4] = 0;
   out_1533656544988368493[5] = 0;
   out_1533656544988368493[6] = 1;
   out_1533656544988368493[7] = 0;
   out_1533656544988368493[8] = 0;
   out_1533656544988368493[9] = 1;
   out_1533656544988368493[10] = 0;
   out_1533656544988368493[11] = 0;
   out_1533656544988368493[12] = 0;
   out_1533656544988368493[13] = 0;
   out_1533656544988368493[14] = 0;
   out_1533656544988368493[15] = 0;
   out_1533656544988368493[16] = 0;
   out_1533656544988368493[17] = 0;
   out_1533656544988368493[18] = 0;
   out_1533656544988368493[19] = 0;
   out_1533656544988368493[20] = 0;
   out_1533656544988368493[21] = 0;
   out_1533656544988368493[22] = 0;
   out_1533656544988368493[23] = 0;
   out_1533656544988368493[24] = 0;
   out_1533656544988368493[25] = 1;
   out_1533656544988368493[26] = 0;
   out_1533656544988368493[27] = 0;
   out_1533656544988368493[28] = 1;
   out_1533656544988368493[29] = 0;
   out_1533656544988368493[30] = 0;
   out_1533656544988368493[31] = 0;
   out_1533656544988368493[32] = 0;
   out_1533656544988368493[33] = 0;
   out_1533656544988368493[34] = 0;
   out_1533656544988368493[35] = 0;
   out_1533656544988368493[36] = 0;
   out_1533656544988368493[37] = 0;
   out_1533656544988368493[38] = 0;
   out_1533656544988368493[39] = 0;
   out_1533656544988368493[40] = 0;
   out_1533656544988368493[41] = 0;
   out_1533656544988368493[42] = 0;
   out_1533656544988368493[43] = 0;
   out_1533656544988368493[44] = 1;
   out_1533656544988368493[45] = 0;
   out_1533656544988368493[46] = 0;
   out_1533656544988368493[47] = 1;
   out_1533656544988368493[48] = 0;
   out_1533656544988368493[49] = 0;
   out_1533656544988368493[50] = 0;
   out_1533656544988368493[51] = 0;
   out_1533656544988368493[52] = 0;
   out_1533656544988368493[53] = 0;
}
void h_10(double *state, double *unused, double *out_9133294609098034128) {
   out_9133294609098034128[0] = 9.8100000000000005*sin(state[1]) - state[4]*state[8] + state[5]*state[7] + state[12] + state[15];
   out_9133294609098034128[1] = -9.8100000000000005*sin(state[0])*cos(state[1]) + state[3]*state[8] - state[5]*state[6] + state[13] + state[16];
   out_9133294609098034128[2] = -9.8100000000000005*cos(state[0])*cos(state[1]) - state[3]*state[7] + state[4]*state[6] + state[14] + state[17];
}
void H_10(double *state, double *unused, double *out_1083055239647106891) {
   out_1083055239647106891[0] = 0;
   out_1083055239647106891[1] = 9.8100000000000005*cos(state[1]);
   out_1083055239647106891[2] = 0;
   out_1083055239647106891[3] = 0;
   out_1083055239647106891[4] = -state[8];
   out_1083055239647106891[5] = state[7];
   out_1083055239647106891[6] = 0;
   out_1083055239647106891[7] = state[5];
   out_1083055239647106891[8] = -state[4];
   out_1083055239647106891[9] = 0;
   out_1083055239647106891[10] = 0;
   out_1083055239647106891[11] = 0;
   out_1083055239647106891[12] = 1;
   out_1083055239647106891[13] = 0;
   out_1083055239647106891[14] = 0;
   out_1083055239647106891[15] = 1;
   out_1083055239647106891[16] = 0;
   out_1083055239647106891[17] = 0;
   out_1083055239647106891[18] = -9.8100000000000005*cos(state[0])*cos(state[1]);
   out_1083055239647106891[19] = 9.8100000000000005*sin(state[0])*sin(state[1]);
   out_1083055239647106891[20] = 0;
   out_1083055239647106891[21] = state[8];
   out_1083055239647106891[22] = 0;
   out_1083055239647106891[23] = -state[6];
   out_1083055239647106891[24] = -state[5];
   out_1083055239647106891[25] = 0;
   out_1083055239647106891[26] = state[3];
   out_1083055239647106891[27] = 0;
   out_1083055239647106891[28] = 0;
   out_1083055239647106891[29] = 0;
   out_1083055239647106891[30] = 0;
   out_1083055239647106891[31] = 1;
   out_1083055239647106891[32] = 0;
   out_1083055239647106891[33] = 0;
   out_1083055239647106891[34] = 1;
   out_1083055239647106891[35] = 0;
   out_1083055239647106891[36] = 9.8100000000000005*sin(state[0])*cos(state[1]);
   out_1083055239647106891[37] = 9.8100000000000005*sin(state[1])*cos(state[0]);
   out_1083055239647106891[38] = 0;
   out_1083055239647106891[39] = -state[7];
   out_1083055239647106891[40] = state[6];
   out_1083055239647106891[41] = 0;
   out_1083055239647106891[42] = state[4];
   out_1083055239647106891[43] = -state[3];
   out_1083055239647106891[44] = 0;
   out_1083055239647106891[45] = 0;
   out_1083055239647106891[46] = 0;
   out_1083055239647106891[47] = 0;
   out_1083055239647106891[48] = 0;
   out_1083055239647106891[49] = 0;
   out_1083055239647106891[50] = 1;
   out_1083055239647106891[51] = 0;
   out_1083055239647106891[52] = 0;
   out_1083055239647106891[53] = 1;
}
void h_13(double *state, double *unused, double *out_7261076778239694426) {
   out_7261076778239694426[0] = state[3];
   out_7261076778239694426[1] = state[4];
   out_7261076778239694426[2] = state[5];
}
void H_13(double *state, double *unused, double *out_4745930370320701294) {
   out_4745930370320701294[0] = 0;
   out_4745930370320701294[1] = 0;
   out_4745930370320701294[2] = 0;
   out_4745930370320701294[3] = 1;
   out_4745930370320701294[4] = 0;
   out_4745930370320701294[5] = 0;
   out_4745930370320701294[6] = 0;
   out_4745930370320701294[7] = 0;
   out_4745930370320701294[8] = 0;
   out_4745930370320701294[9] = 0;
   out_4745930370320701294[10] = 0;
   out_4745930370320701294[11] = 0;
   out_4745930370320701294[12] = 0;
   out_4745930370320701294[13] = 0;
   out_4745930370320701294[14] = 0;
   out_4745930370320701294[15] = 0;
   out_4745930370320701294[16] = 0;
   out_4745930370320701294[17] = 0;
   out_4745930370320701294[18] = 0;
   out_4745930370320701294[19] = 0;
   out_4745930370320701294[20] = 0;
   out_4745930370320701294[21] = 0;
   out_4745930370320701294[22] = 1;
   out_4745930370320701294[23] = 0;
   out_4745930370320701294[24] = 0;
   out_4745930370320701294[25] = 0;
   out_4745930370320701294[26] = 0;
   out_4745930370320701294[27] = 0;
   out_4745930370320701294[28] = 0;
   out_4745930370320701294[29] = 0;
   out_4745930370320701294[30] = 0;
   out_4745930370320701294[31] = 0;
   out_4745930370320701294[32] = 0;
   out_4745930370320701294[33] = 0;
   out_4745930370320701294[34] = 0;
   out_4745930370320701294[35] = 0;
   out_4745930370320701294[36] = 0;
   out_4745930370320701294[37] = 0;
   out_4745930370320701294[38] = 0;
   out_4745930370320701294[39] = 0;
   out_4745930370320701294[40] = 0;
   out_4745930370320701294[41] = 1;
   out_4745930370320701294[42] = 0;
   out_4745930370320701294[43] = 0;
   out_4745930370320701294[44] = 0;
   out_4745930370320701294[45] = 0;
   out_4745930370320701294[46] = 0;
   out_4745930370320701294[47] = 0;
   out_4745930370320701294[48] = 0;
   out_4745930370320701294[49] = 0;
   out_4745930370320701294[50] = 0;
   out_4745930370320701294[51] = 0;
   out_4745930370320701294[52] = 0;
   out_4745930370320701294[53] = 0;
}
void h_14(double *state, double *unused, double *out_3309539515902900024) {
   out_3309539515902900024[0] = state[6];
   out_3309539515902900024[1] = state[7];
   out_3309539515902900024[2] = state[8];
}
void H_14(double *state, double *unused, double *out_5496897401327853022) {
   out_5496897401327853022[0] = 0;
   out_5496897401327853022[1] = 0;
   out_5496897401327853022[2] = 0;
   out_5496897401327853022[3] = 0;
   out_5496897401327853022[4] = 0;
   out_5496897401327853022[5] = 0;
   out_5496897401327853022[6] = 1;
   out_5496897401327853022[7] = 0;
   out_5496897401327853022[8] = 0;
   out_5496897401327853022[9] = 0;
   out_5496897401327853022[10] = 0;
   out_5496897401327853022[11] = 0;
   out_5496897401327853022[12] = 0;
   out_5496897401327853022[13] = 0;
   out_5496897401327853022[14] = 0;
   out_5496897401327853022[15] = 0;
   out_5496897401327853022[16] = 0;
   out_5496897401327853022[17] = 0;
   out_5496897401327853022[18] = 0;
   out_5496897401327853022[19] = 0;
   out_5496897401327853022[20] = 0;
   out_5496897401327853022[21] = 0;
   out_5496897401327853022[22] = 0;
   out_5496897401327853022[23] = 0;
   out_5496897401327853022[24] = 0;
   out_5496897401327853022[25] = 1;
   out_5496897401327853022[26] = 0;
   out_5496897401327853022[27] = 0;
   out_5496897401327853022[28] = 0;
   out_5496897401327853022[29] = 0;
   out_5496897401327853022[30] = 0;
   out_5496897401327853022[31] = 0;
   out_5496897401327853022[32] = 0;
   out_5496897401327853022[33] = 0;
   out_5496897401327853022[34] = 0;
   out_5496897401327853022[35] = 0;
   out_5496897401327853022[36] = 0;
   out_5496897401327853022[37] = 0;
   out_5496897401327853022[38] = 0;
   out_5496897401327853022[39] = 0;
   out_5496897401327853022[40] = 0;
   out_5496897401327853022[41] = 0;
   out_5496897401327853022[42] = 0;
   out_5496897401327853022[43] = 0;
   out_5496897401327853022[44] = 1;
   out_5496897401327853022[45] = 0;
   out_5496897401327853022[46] = 0;
   out_5496897401327853022[47] = 0;
   out_5496897401327853022[48] = 0;
   out_5496897401327853022[49] = 0;
   out_5496897401327853022[50] = 0;
   out_5496897401327853022[51] = 0;
   out_5496897401327853022[52] = 0;
   out_5496897401327853022[53] = 0;
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
void pose_err_fun(double *nom_x, double *delta_x, double *out_944171533359057280) {
  err_fun(nom_x, delta_x, out_944171533359057280);
}
void pose_inv_err_fun(double *nom_x, double *true_x, double *out_5987343215061063396) {
  inv_err_fun(nom_x, true_x, out_5987343215061063396);
}
void pose_H_mod_fun(double *state, double *out_922696574028504956) {
  H_mod_fun(state, out_922696574028504956);
}
void pose_f_fun(double *state, double dt, double *out_7271275588779717019) {
  f_fun(state,  dt, out_7271275588779717019);
}
void pose_F_fun(double *state, double dt, double *out_1488608338000593967) {
  F_fun(state,  dt, out_1488608338000593967);
}
void pose_h_4(double *state, double *unused, double *out_7790743839692054955) {
  h_4(state, unused, out_7790743839692054955);
}
void pose_H_4(double *state, double *unused, double *out_1533656544988368493) {
  H_4(state, unused, out_1533656544988368493);
}
void pose_h_10(double *state, double *unused, double *out_9133294609098034128) {
  h_10(state, unused, out_9133294609098034128);
}
void pose_H_10(double *state, double *unused, double *out_1083055239647106891) {
  H_10(state, unused, out_1083055239647106891);
}
void pose_h_13(double *state, double *unused, double *out_7261076778239694426) {
  h_13(state, unused, out_7261076778239694426);
}
void pose_H_13(double *state, double *unused, double *out_4745930370320701294) {
  H_13(state, unused, out_4745930370320701294);
}
void pose_h_14(double *state, double *unused, double *out_3309539515902900024) {
  h_14(state, unused, out_3309539515902900024);
}
void pose_H_14(double *state, double *unused, double *out_5496897401327853022) {
  H_14(state, unused, out_5496897401327853022);
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
