"""Detector de placas que usa a biblioteca P2 (load / infer / info / release).

Faz o papel do `YOLO(best.pt)` da ultralytics: expõe `names` e `predict(img, conf)` e devolve um
objeto com `.boxes` no mesmo formato (cada caixa tem `.conf[0]`, `.cls[0]` e `.xyxy[0]`), então o
pipeline não precisa saber qual dos dois detectores está sendo usado.

O P2 só executa o modelo. O pré-processamento (BGR -> RGB, letterbox 114, /255) e o pós-processamento
(limiar, NMS, volta para as coordenadas da imagem) são do consumidor; a lógica abaixo segue o
exemplo `examples/yolo_demo.py` do repositório do P2, sem alterar o P2.

O `import p2` é feito só ao criar o detector, para quem usa a ultralytics não precisar do P2.
"""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

COR_PREENCHIMENTO = 114      # cinza usado pelo YOLO nas bordas do letterbox
ESCALA_PIXEL = 255.0
LINHAS_CAIXA = 4             # a saída tem 4 linhas de caixa + 1 linha por classe
IOU_PADRAO = 0.7             # mesmo padrão da ultralytics, para os dois detectores serem comparáveis
RAIZ_ENGINES_PADRAO = "/opt/ods/engines"


def _letterbox(img_bgr: np.ndarray, lado: int):
    """Redimensiona mantendo a proporção, completa com cinza até lado x lado e devolve o tensor."""
    altura, largura = img_bgr.shape[:2]
    escala = min(lado / altura, lado / largura)
    nova_largura, nova_altura = round(largura * escala), round(altura * escala)
    redimensionada = cv2.resize(img_bgr, (nova_largura, nova_altura), interpolation=cv2.INTER_LINEAR)
    pad_x, pad_y = (lado - nova_largura) / 2, (lado - nova_altura) / 2
    topo, esquerda = round(pad_y - 0.1), round(pad_x - 0.1)
    tela = np.full((lado, lado, 3), COR_PREENCHIMENTO, dtype=np.uint8)
    tela[topo:topo + nova_altura, esquerda:esquerda + nova_largura] = redimensionada
    rgb = tela[:, :, ::-1]
    tensor = np.ascontiguousarray(rgb.transpose(2, 0, 1)[np.newaxis], dtype=np.float32) / np.float32(ESCALA_PIXEL)
    return tensor, escala, pad_x, pad_y


def _iou(caixa: np.ndarray, caixas: np.ndarray) -> np.ndarray:
    canto_sup = np.maximum(caixa[:2], caixas[:, :2])
    canto_inf = np.minimum(caixa[2:], caixas[:, 2:])
    lados = np.clip(canto_inf - canto_sup, 0, None)
    inter = lados[:, 0] * lados[:, 1]
    area = (caixa[2] - caixa[0]) * (caixa[3] - caixa[1])
    areas = (caixas[:, 2] - caixas[:, 0]) * (caixas[:, 3] - caixas[:, 1])
    return inter / np.maximum(area + areas - inter, 1e-9)


def _nms(caixas: np.ndarray, notas: np.ndarray, classes: np.ndarray, iou: float) -> list[int]:
    """Supressão de caixas sobrepostas, classe por classe; devolve os índices mantidos (maior nota primeiro)."""
    mantidas: list[int] = []
    for classe in np.unique(classes):
        ordem = np.flatnonzero(classes == classe)
        ordem = ordem[np.argsort(-notas[ordem], kind="stable")]
        while ordem.size:
            melhor, resto = int(ordem[0]), ordem[1:]
            mantidas.append(melhor)
            ordem = resto[_iou(caixas[melhor], caixas[resto]) <= iou]
    return sorted(mantidas, key=lambda i: -float(notas[i]))


class DetectorP2:
    """Carrega o engine pelo `engine_ref` (ex.: app-plates-1.0.0) e detecta placas em imagens BGR."""

    def __init__(self, engine_ref: str, iou: float = IOU_PADRAO):
        import p2   # import tardio: só quem usa DETECTOR=p2 precisa da biblioteca

        self._p2 = p2
        self.engine_ref = engine_ref
        self.iou = iou
        self._handle = p2.load(engine_ref)
        info = p2.info(self._handle)
        ((self._nome_entrada, forma_entrada),) = info["input_shape"].items()
        ((self._nome_saida, _),) = info["output_shape"].items()
        self._lado = int(forma_entrada[2])
        self.versao = info["engine_version"]      # valor a publicar como artifact_version
        self.names = dict(enumerate(self._ler_classes(engine_ref)))

    @staticmethod
    def _ler_classes(engine_ref: str) -> list[str]:
        raiz = Path(os.environ.get("P2_ENGINE_DIR") or RAIZ_ENGINES_PADRAO)
        metadados = json.loads((raiz / engine_ref / "metadata.json").read_text(encoding="utf-8"))
        return [str(nome) for nome in metadados["classes"]]

    def predict(self, img_bgr: np.ndarray, conf: float = 0.25, verbose: bool = False):
        altura, largura = img_bgr.shape[:2]
        tensor, escala, pad_x, pad_y = _letterbox(img_bgr, self._lado)
        saidas, _ = self._p2.infer(self._handle, {self._nome_entrada: tensor})
        previsoes = np.asarray(saidas[self._nome_saida], dtype=np.float32)[0].T      # (8400, 4 + classes)
        notas_classes = previsoes[:, LINHAS_CAIXA:]
        classes = notas_classes.argmax(axis=1).astype(np.int64)
        notas = notas_classes.max(axis=1)
        manter = notas >= conf
        centros, notas, classes = previsoes[manter, :LINHAS_CAIXA], notas[manter], classes[manter]
        metade = centros[:, 2:4] / 2
        caixas = np.concatenate([centros[:, :2] - metade, centros[:, :2] + metade], axis=1)
        deslocamento = np.array([pad_x, pad_y, pad_x, pad_y], dtype=np.float32)
        caixas = (caixas - deslocamento) / np.float32(escala)
        caixas = np.clip(caixas, 0, np.array([largura, altura, largura, altura], dtype=np.float32))
        indices = _nms(caixas, notas, classes, self.iou)
        return [SimpleNamespace(boxes=[
            SimpleNamespace(conf=[float(notas[i])], cls=[int(classes[i])], xyxy=[caixas[i]])
            for i in indices
        ])]

    def release(self) -> None:
        self._p2.release(self._handle)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.release()