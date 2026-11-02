from ultralytics import YOLO


model = YOLO("best.pt")
model.predict(
    source="cars_extended.mp4",
    imgsz=1280,
    save=True,
    name="video_predictions"
)


