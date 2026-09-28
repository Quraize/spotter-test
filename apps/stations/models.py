from django.db import models


class StationQuerySet(models.QuerySet):
    def geocoded(self) -> "StationQuerySet":
        return self.filter(latitude__isnull=False, longitude__isnull=False)


class Station(models.Model):
    """
    A truck stop with a diesel retail price, from the OPIS price file.

    Coordinates come from the offline geocoding step (see ``geocode_stations``); a station
    without coordinates is kept for completeness but never used for planning.
    """

    class GeocodeSource(models.TextChoices):
        LOCAL = "local", "Local city table"
        NOMINATIM = "nominatim", "Nominatim"
        NONE = "", "Not geocoded"

    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    rack_id = models.PositiveIntegerField()
    retail_price = models.DecimalField(max_digits=8, decimal_places=5)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    geocode_source = models.CharField(
        max_length=20, choices=GeocodeSource.choices, blank=True, default=GeocodeSource.NONE
    )

    objects = StationQuerySet.as_manager()

    class Meta:
        ordering = ["opis_id"]
        indexes = [
            models.Index(fields=["state", "city"], name="station_state_city_idx"),
            models.Index(fields=["latitude", "longitude"], name="station_latlng_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.city}, {self.state}) ${self.retail_price}"
