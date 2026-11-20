"""
Baixa um vídeo (por exemplo, do YouTube) para a pasta videos/, para depois rodar o pipeline com
--video. Usa a biblioteca yt-dlp.

Baixa só a imagem (sem áudio), em MP4 quando houver, com altura de até --altura-max pixels. Assim o
arquivo sai menor e não é preciso juntar áudio e vídeo (o que exigiria o ffmpeg).

Uso:
    python scripts/baixar_video.py "https://www.youtube.com/watch?v=..."
    python scripts/baixar_video.py "https://www.youtube.com/watch?v=..." --nome portao --altura-max 720

Depois:
    python scripts/aplicacaoModelov5-fast+rodosol.py --video videos/<nome>.mp4

Use só vídeos que vocês possam usar (os termos do YouTube restringem downloads) e de preferência
curtos: o pipeline processa cerca de 2 quadros por segundo na CPU.
"""
import argparse
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTA_VIDEOS = RAIZ / "videos"


def nome_seguro(texto: str) -> str:
    """Nome de arquivo só com letras, números, '-' e '_' (sem espaços nem acentos)."""
    texto = re.sub(r"[^A-Za-z0-9_-]+", "_", texto).strip("_")
    return texto[:60] or "video"


def main() -> int:
    parser = argparse.ArgumentParser(description="Baixa um vídeo para a pasta videos/ (usa yt-dlp).")
    parser.add_argument("url", help="endereço do vídeo (ex.: link do YouTube)")
    parser.add_argument("--nome", help="nome do arquivo, sem extensão (padrão: o id do vídeo)")
    parser.add_argument("--altura-max", type=int, default=1080,
                        help="altura máxima em pixels (padrão 1080)")
    args = parser.parse_args()

    try:
        import yt_dlp
    except ImportError:
        print("[ERRO] Instale a biblioteca: pip install yt-dlp (no Docker, rode o build de novo)")
        return 2

    h = args.altura_max
    PASTA_VIDEOS.mkdir(exist_ok=True)
    opcoes = {
        # só vídeo (sem áudio): MP4 de preferência; senão, o melhor disponível até essa altura
        "format": f"bv*[ext=mp4][height<={h}]/bv*[height<={h}]/b[height<={h}]/b",
        "outtmpl": str(PASTA_VIDEOS / ((nome_seguro(args.nome) if args.nome else "%(id)s") + ".%(ext)s")),
        "noplaylist": True,          # link de playlist: baixa só o vídeo indicado
        "restrictfilenames": True,
        "overwrites": True,
    }
    try:
        with yt_dlp.YoutubeDL(opcoes) as ydl:
            info = ydl.extract_info(args.url, download=True)
            arquivo = Path(ydl.prepare_filename(info))
    except Exception as erro:   # yt_dlp.utils.DownloadError, rede, vídeo privado...
        print(f"[ERRO] Não foi possível baixar: {erro}")
        print("Alternativa: baixe o vídeo por outro meio e coloque o arquivo na pasta videos/.")
        return 1

    duracao = info.get("duration")
    print(f"\nVídeo salvo em: videos/{arquivo.name}"
          + (f" ({info.get('height')}p, {duracao:.0f} s)" if duracao else ""))
    print(f"Para processar: scripts/aplicacaoModelov5-fast+rodosol.py --video videos/{arquivo.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())