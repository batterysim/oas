# Variable holding executable scripts
set(SCRIPTS
      fcContainerize.py
)

# Installing executable scripts
install(PROGRAMS
  ${SCRIPTS}
  DESTINATION bin
  PERMISSIONS OWNER_READ OWNER_EXECUTE OWNER_WRITE
              GROUP_READ GROUP_EXECUTE ${SCI_GROUP_WRITE} ${SCI_WORLD_PERMS}
)

# Link data and scripts to build dir
if (NOT WIN32)
add_custom_target(toplevel-bin-scripts-stamp ALL
  COMMAND ${CMAKE_SOURCE_DIR}/scimake/mklinks.sh txutils-scripts-stamp
    ${CMAKE_CURRENT_SOURCE_DIR} ${DATA} ${SCRIPTS}
  COMMAND chmod a+x *.py
)
endif ()

