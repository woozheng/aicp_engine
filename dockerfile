FROM python:3.11-slim

WORKDIR /app

COPY requirements-server.txt .
RUN pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-server.txt

COPY . .

EXPOSE 9000 9001 9002

CMD ["python", "-m", "runtime", "--server"]