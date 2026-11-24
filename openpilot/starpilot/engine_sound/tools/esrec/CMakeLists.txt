cmake_minimum_required(VERSION 3.16)
project(esrec CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_BUILD_TYPE Release)
set(ENGINE_SIM_DIR "" CACHE PATH "engine-sim checkout with submodules and engine-sim-linux.patch applied")
if(NOT ENGINE_SIM_DIR)
  message(FATAL_ERROR "Pass -DENGINE_SIM_DIR=<engine-sim checkout>")
endif()
set(ES ${ENGINE_SIM_DIR})
add_compile_definitions(ATG_ENGINE_SIM_PIRANHA_ENABLED __forceinline=inline)
add_compile_options("SHELL:-include ${CMAKE_CURRENT_SOURCE_DIR}/prelude.h" -w -fdelayed-template-parsing -fms-extensions)
add_subdirectory(${ES}/dependencies/submodules/simple-2d-constraint-solver scs EXCLUDE_FROM_ALL)
add_subdirectory(${ES}/dependencies/submodules/csv-io csvio EXCLUDE_FROM_ALL)
add_subdirectory(${ES}/dependencies/submodules/piranha piranha EXCLUDE_FROM_ALL)
set(CORE_SOURCES
  ${ES}/src/audio_buffer.cpp
  ${ES}/src/camshaft.cpp
  ${ES}/src/crankshaft.cpp
  ${ES}/src/combustion_chamber.cpp
  ${ES}/src/connecting_rod.cpp
  ${ES}/src/convolution_filter.cpp
  ${ES}/src/cylinder_bank.cpp
  ${ES}/src/cylinder_head.cpp
  ${ES}/src/delay_filter.cpp
  ${ES}/src/derivative_filter.cpp
  ${ES}/src/direct_throttle_linkage.cpp
  ${ES}/src/dynamometer.cpp
  ${ES}/src/engine.cpp
  ${ES}/src/exhaust_system.cpp
  ${ES}/src/feedback_comb_filter.cpp
  ${ES}/src/filter.cpp
  ${ES}/src/fuel.cpp
  ${ES}/src/function.cpp
  ${ES}/src/gas_system.cpp
  ${ES}/src/gaussian_filter.cpp
  ${ES}/src/governor.cpp
  ${ES}/src/ignition_module.cpp
  ${ES}/src/impulse_response.cpp
  ${ES}/src/intake.cpp
  ${ES}/src/jitter_filter.cpp
  ${ES}/src/leveling_filter.cpp
  ${ES}/src/low_pass_filter.cpp
  ${ES}/src/part.cpp
  ${ES}/src/piston.cpp
  ${ES}/src/piston_engine_simulator.cpp
  ${ES}/src/simulator.cpp
  ${ES}/src/standard_valvetrain.cpp
  ${ES}/src/starter_motor.cpp
  ${ES}/src/synthesizer.cpp
  ${ES}/src/throttle.cpp
  ${ES}/src/transmission.cpp
  ${ES}/src/utilities.cpp
  ${ES}/src/valvetrain.cpp
  ${ES}/src/vehicle.cpp
  ${ES}/src/vehicle_drag_constraint.cpp
  ${ES}/src/vtec_valvetrain.cpp
)
add_library(es-core STATIC ${CORE_SOURCES})
target_include_directories(es-core PUBLIC ${ES}/dependencies/submodules)
target_link_libraries(es-core simple-2d-constraint-solver csv-io)
file(GLOB SCRIPT_SOURCES ${ES}/scripting/src/*.cpp)
add_library(es-script STATIC ${SCRIPT_SOURCES})
target_include_directories(es-script PUBLIC ${ES}/dependencies/submodules)
target_link_libraries(es-script es-core csv-io piranha)
add_executable(esrec esrec.cpp)
target_include_directories(esrec PRIVATE ${ES})
target_link_libraries(esrec es-script es-core pthread)
target_link_options(esrec PRIVATE -Wl,--allow-multiple-definition)
