from dataclasses import dataclass

__all__ = ("SurveyInSettings",)


@dataclass
class SurveyInSettings:
    """Dataclass containing the settings of a survey-in procedure that the user
    wishes to execute on an RTK data source if the data source supports custom
    survey-in settings.
    """

    #: Minimum duration of the survey-in, in seconds
    duration: float = 60

    #: Desired accuracy of the survey-in, in meters
    accuracy: float = 0.02

    @property
    def json(self):
        return {"duration": self.duration, "accuracy": self.accuracy}
