# Imagen del estudio de Rodaje (clase 4 de Prompt Models): ComfyUI 0.44.2, nodos fijados por commit
# y todas sus dependencias. Sin modelos, sin keys y sin datos de nadie: los modelos se bajan al prender.
FROM runpod/comfyui:1.3.3-comfyuiv0.30.0-cuda13.0
ARG PMS_ETIQUETA=dev
LABEL org.opencontainers.image.source="https://github.com/cdanielp/pms-rodaje-imagen" \
      org.opencontainers.image.description="ComfyUI 0.44.2 + nodos H3 fijados (clase 4 Rodaje). Sin modelos." \
      org.opencontainers.image.licenses="MIT"
COPY instalar_rodaje.py modelos.json /opt/pms/build/
# El mismo instalador de las máquinas, en modo --imagen: instala ComfyUI, nodos y dependencias y deja una marca.
RUN mkdir -p /opt/pms/build/workflows \
 && (PMS_TMP=/tmp/pms_build PMS_ETIQUETA=${PMS_ETIQUETA} python3 /opt/pms/build/instalar_rodaje.py --imagen \
     || (tail -n 80 /tmp/pms_build/instalacion.log; exit 1)) \
 && mv /opt/pms/ComfyUI/models /opt/pms/models_base \
 && ln -s /workspace/pms_models /opt/pms/ComfyUI/models \
 && rm -rf /tmp/pms_build /root/.cache /opt/pms/build \
 && find /opt/pms/ComfyUI -name __pycache__ -prune -exec rm -rf {} + \
 && echo "permisos antes: $(stat -c '%a %U' /root)" \
 && chmod 700 /root && mkdir -p /root/.ssh && chmod 700 /root/.ssh \
 && echo "permisos despues: $(stat -c '%a %U' /root /root/.ssh | tr '\n' ' ')" \
 && cat /opt/pms/IMAGEN_PMS.json
