"""
Treinamento do YOLO para detecção de placas veiculares (1 classe: "plate").

Requisitos:
    pip install "ultralytics>=8.3"
    (e PyTorch com CUDA para usar a GPU)

Uso:
    1) Rode antes o converter_dataset_yolo.py para gerar o data.yaml.
    2) Ajuste DATA_YAML abaixo.
    3) python treinar_yolo_placas.py
"""

from ultralytics import YOLO

# =============================================================================
# CONFIGURAÇÕES GLOBAIS
# =============================================================================
DATA_YAML = "/caminho/para/dataset_yolo/data.yaml"
PROJECT_DIR = "runs_placas"       # pasta onde os resultados serão salvos
RUN_NAME = "yolo_placa_v1"        # nome desta execução
SEED = 42

# Modelo base (pré-treinado no COCO -> transfer learning).
# "n" = mais rápido/leve, "s" = melhor equilíbrio, "m" = mais preciso e mais pesado.
# Para uma cancela (1 câmera, 1 classe, cena simples), "s" costuma ser o ponto ideal.
# Se quiser testar rápido o pipeline, use "yolo11n.pt".
MODEL_WEIGHTS = "yolo11s.pt"

DEVICE = 0          # 0 = primeira GPU | "cpu" = CPU (muito lento) | "0,1" = duas GPUs
IMG_SIZE = 1280     # ver comentário detalhado abaixo
BATCH = 8           # reduza para 4 se der "CUDA out of memory"; -1 = automático
EPOCHS = 100
WORKERS = 8         # threads de leitura de dados (reduza se a CPU/RAM ficar sobrecarregada)


def main():
    model = YOLO(MODEL_WEIGHTS)

    model.train(
        # ------------------------------------------------------------------
        # DADOS E BÁSICO
        # ------------------------------------------------------------------
        data=DATA_YAML,
        project=PROJECT_DIR,
        name=RUN_NAME,
        seed=SEED,
        deterministic=True,   # resultados reproduzíveis (um pouco mais lento; útil p/ comparar experimentos)

        # single_cls: trata tudo como uma única classe. Aqui só existe "plate", então é
        # redundante, mas garante que nenhum rótulo extra vire outra classe por engano.
        single_cls=True,

        # ------------------------------------------------------------------
        # RESOLUÇÃO  (o hiperparâmetro MAIS importante para o seu caso)
        # ------------------------------------------------------------------
        # imgsz=1280: as imagens originais são 1280x720 e a placa ocupa só ~130x44 px.
        # Com imgsz=640 ela cairia para ~65x22 px, perto do limite do que o detector
        # enxerga bem. Em 1280 mantemos a resolução nativa (sem perder detalhe) e o
        # recorte da placa também fica mais preciso. O custo é mais VRAM e tempo de
        # treino, por isso o batch é pequeno. Dica: valide o pipeline com 640 e depois
        # suba para 1280 comparando o mAP.
        imgsz=IMG_SIZE,

        # ------------------------------------------------------------------
        # DURAÇÃO E PARADA ANTECIPADA
        # ------------------------------------------------------------------
        # epochs: máximo de passadas pelo dataset. Com ~14 mil imagens de treino e
        # modelo pré-treinado, 100 épocas é bastante; o early stopping abaixo
        # normalmente encerra antes.
        epochs=EPOCHS,

        # patience: se o mAP de validação não melhorar por 25 épocas seguidas, para o
        # treino. Evita desperdiçar horas de GPU e reduz risco de overfitting.
        patience=25,

        # ------------------------------------------------------------------
        # BATCH E HARDWARE
        # ------------------------------------------------------------------
        # batch: imagens por passo de otimização. Em 1280px, batch 8 cabe numa GPU de
        # ~8 GB com yolo11s. Batch muito pequeno (<4) deixa o BatchNorm instável;
        # muito grande estoura a memória.
        batch=BATCH,
        device=DEVICE,
        workers=WORKERS,

        # amp: treino em precisão mista (FP16/FP32). ~2x mais rápido e usa menos VRAM,
        # praticamente sem perda de qualidade.
        amp=True,

        # cache: guardar as imagens decodificadas na RAM acelera as épocas, mas 14 mil
        # imagens 1280x720 ocupam dezenas de GB. Deixe False, a menos que tenha RAM de sobra
        # (nesse caso, use "ram"; "disk" é um meio-termo).
        cache=False,

        # ------------------------------------------------------------------
        # OTIMIZADOR E LEARNING RATE
        # ------------------------------------------------------------------
        # SGD com lr0=0.01 é a configuração clássica e estável do YOLO para datasets
        # grandes (é o que o modo "auto" escolheria para um treino longo como este).
        # Fixar explicitamente torna o experimento reproduzível.
        optimizer="SGD",
        lr0=0.01,             # learning rate inicial: passo de aprendizado no começo
        lrf=0.01,             # lr final = lr0 * lrf (o LR decai até 1% do inicial)
        momentum=0.937,       # "inércia" do SGD; suaviza as atualizações
        weight_decay=0.0005,  # regularização L2: penaliza pesos grandes, reduz overfitting

        # cos_lr: decaimento do LR em formato de cosseno (suave) em vez de linear.
        # Costuma dar convergência final um pouco melhor.
        cos_lr=True,

        # warmup_epochs: nas 3 primeiras épocas o LR sobe gradualmente. Protege os
        # pesos pré-treinados de "choques" no início, quando o modelo ainda está
        # se adaptando à nova tarefa.
        warmup_epochs=3.0,

        # ------------------------------------------------------------------
        # DATA AUGMENTATION (ajustada para PLACAS / CANCELA)
        # A ideia: simular variações reais (luz, distância, leve inclinação)
        # sem criar situações impossíveis (placa de cabeça para baixo, etc.).
        # ------------------------------------------------------------------

        # mosaic: cola 4 imagens em uma só. Faz a placa aparecer em posições e escalas
        # variadas e em contextos diferentes. Muito eficaz para detecção de objetos pequenos.
        mosaic=1.0,

        # close_mosaic: desliga o mosaic nas últimas 10 épocas. As imagens "colagem"
        # não existem na vida real; terminar com imagens normais alinha o modelo
        # à distribuição real de uso e melhora a métrica final.
        close_mosaic=10,

        # fliplr=0.0: NÃO espelhar horizontalmente. Placas espelhadas não existem e o
        # texto invertido só confundiria o modelo (e o recorte que vai para o OCR).
        fliplr=0.0,
        # flipud=0.0: NÃO virar de cabeça para baixo. Câmera de cancela nunca vê isso.
        flipud=0.0,

        # scale: varia o tamanho do objeto em +-50%. Simula veículos parando mais
        # perto ou mais longe da câmera. Muito importante para robustez de distância.
        scale=0.5,

        # translate: desloca a imagem em até 10%. A placa nem sempre estará no mesmo lugar do frame.
        translate=0.1,

        # degrees: rotação de +-3 graus. Simula câmera levemente torta ou veículo em
        # curva. Valores altos deixariam a placa em ângulos irreais.
        degrees=3.0,

        # shear / perspective: pequenas distorções de perspectiva imitam ângulos de
        # câmera diferentes. Mantidos baixos porque a placa é um objeto pequeno e
        # rígido e distorção forte a deformaria demais.
        shear=1.0,
        perspective=0.0005,

        # hsv_h / hsv_s / hsv_v: variações de matiz, saturação e brilho. Simulam dia,
        # noite, sol forte, sombra e cores diferentes de veículo. hsv_v (brilho) alto é
        # importante porque a iluminação é o maior fator de variação em portaria/cancela.
        hsv_h=0.015,
        hsv_s=0.5,
        hsv_v=0.4,

        # mixup e copy_paste: misturam imagens/objetos. Não ajudam em detecção de um
        # único objeto pequeno e nítido (podem até atrapalhar), então ficam desligados.
        mixup=0.0,
        copy_paste=0.0,

        # ------------------------------------------------------------------
        # VALIDAÇÃO E SAÍDAS
        # ------------------------------------------------------------------
        val=True,            # valida a cada época (base do early stopping e do "best.pt")
        plots=True,          # gera curvas de loss, PR, matriz de confusão e exemplos
        save_period=-1,      # -1 = salva só last.pt e best.pt (troque para 10 p/ checkpoint a cada 10 épocas)
        exist_ok=False,      # não sobrescreve execuções anteriores (cria yolo_placa_v12, ...)
        pretrained=True,     # parte dos pesos do COCO: converge bem mais rápido que do zero
    )

    # ----------------------------------------------------------------------
    # AVALIAÇÃO FINAL NO CONJUNTO DE TESTE
    # O "test" nunca foi usado no treino nem para escolher o melhor modelo,
    # então é a estimativa mais honesta de desempenho real.
    # ----------------------------------------------------------------------
    best = YOLO(f"{PROJECT_DIR}/{RUN_NAME}/weights/best.pt")
    metricas = best.val(data=DATA_YAML, split="test", imgsz=IMG_SIZE, batch=BATCH, device=DEVICE)

    print("\n===== RESULTADO NO TESTE =====")
    print(f"Precisão:  {metricas.box.mp:.4f}")
    print(f"Recall:    {metricas.box.mr:.4f}")
    print(f"mAP@0.50:  {metricas.box.map50:.4f}")
    print(f"mAP@0.50-0.95: {metricas.box.map:.4f}")

    # Opcional: exportar para ONNX (útil para rodar em produção / embarcado).
    # best.export(format="onnx", imgsz=IMG_SIZE)


# O "if __name__" é obrigatório no Windows para o multiprocessamento do DataLoader funcionar.
if __name__ == "__main__":
    main()