FROM freeradius/freeradius-server:3.2.10
COPY raddb/ /etc/raddb

# Los symlinks de mods-enabled se rompen al clonar en Windows; se crean aqui.
RUN ln -sf ../mods-available/ldap /etc/raddb/mods-enabled/ldap \
    && ln -sf ../mods-available/rest /etc/raddb/mods-enabled/rest

CMD ["-X"]
