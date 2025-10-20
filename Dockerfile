# Imagen base de Python
FROM python:3.11-slim

# Variables de entorno
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# Carpeta de trabajo dentro del contenedor
WORKDIR /code

# Copiar solo requirements primero (mejor cache)
COPY requirements.txt .

# Instalar dependencias
RUN pip install --no-cache-dir -r requirements.txt

# Copiar todo el proyecto
COPY . .

# Establecer PYTHONPATH explícito
ENV PYTHONPATH=/code

# Exponer el puerto
EXPOSE 8080

# Comando de arranque — importa desde carpeta app/
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
