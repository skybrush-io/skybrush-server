from dataclasses import dataclass

__all__ = ("SurveySettings",)


@dataclass
class SurveySettings:
    """Dataclass containing the settings of a survey procedure that the user
    wishes to execute on an RTK data source if the data source supports custom
    survey settings.
    """

    #: Minimum duration of the survey, in seconds
    duration: float = 60

    #: Desired accuracy of the survey, in meters
    accuracy: float = 1

    @property
    def json(self):
        return {"duration": self.duration, "accuracy": self.accuracy}
