FROM python:3.11

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=7860 \
    BIND_ADDRESS=0.0.0.0

RUN useradd --create-home --uid 1000 user \
    && install -d -o user -g user /home/user/app
WORKDIR /home/user/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user:user . .

USER user
EXPOSE 7860

CMD ["python", "-m", "bot"]
