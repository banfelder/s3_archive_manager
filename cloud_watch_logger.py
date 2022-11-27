import time
import traceback
import uuid

import boto3

class CloudWatchLogger:
    """A simple wrapper around Boto3's CloudWatch Logs interface
    
    logger = CloudWatchLogger(log_group_name = "foo", app_name = "bar")
    logger.log(message = "Hello, world!")
    logger.log(message = "Held until throttle delay is met.")
    logger.flush()
    """
    
    def __init__(self, log_group_name, app_name, minimum_put_interval_ms = 1000.0, enable_exception_logging = False):
        self.log_group_name = str(log_group_name)
        self.app_name = str(app_name)
        self.log_stream_name = time.strftime("%Y/%m/%dT%H/%M/%S", time.gmtime()) + "/" + self.app_name + "/" + str(uuid.uuid4())
        self.pending_events = []
        self.last_time_messages_sent = 0
        self.logs = boto3.client('logs')
        self.logs.create_log_stream(logGroupName = self.log_group_name, logStreamName = self.log_stream_name)
        self.next_sequence_token = None
        self.minimum_put_interval_ms = minimum_put_interval_ms
        self.enable_exception_logging = enable_exception_logging

    def add_event(self, message, timestamp = None):
        """
        Add an event to the queue of pending events to be sent to the log stream.
        Does not attempt to flush the queue.
        The timestamp should be the number of seconds since the epoch in UTC. This is different
        from the AWS API, which expect the number of milliseconds since the epoch in UTC.
        If the timestamp is not provided, the current time will be used.
        """
        timestamp = timestamp or time.time()
        event = {'timestamp': int(1000.0 * timestamp),
                 'message': str(message)}
        self.pending_events.append(event)

    def flush(self):
        """
        Flush any events pending in the queue.
        """
        if len(self.pending_events) == 0:
            return

        if self.next_sequence_token:
            response = self.logs.put_log_events(logGroupName = self.log_group_name,
                                                logStreamName = self.log_stream_name,
                                                logEvents = self.pending_events,
                                                sequenceToken = self.next_sequence_token)
        else:
            response = self.logs.put_log_events(logGroupName = self.log_group_name,
                                                logStreamName = self.log_stream_name,
                                                logEvents = self.pending_events)
        self.last_time_messages_sent = 1000.0 * time.time()
        self.next_sequence_token = response["nextSequenceToken"]
        self.pending_events.clear()
    
    def log(self, message, timestamp = None):
        """
        Add an event to the queue of pending events to the sent to the log stream, and then send it
        if we have not sent an event in the past minimum put interval.
        The timestamp should be the number of seconds since the epoch in UTC. This is different
        from the AWS API, which expect the number of milliseconds since the epoch in UTC.
        If the timestamp is not provided, the current time will be used.
        """
        self.add_event(message, timestamp)
        if (not self.last_time_messages_sent) or ((1000.0 * time.time() - self.last_time_messages_sent) > self.minimum_put_interval_ms):
            self.flush()
    
    def __enter__(self):
        self.log("Logging started")
        return self
    
    def __exit__(self, exc_type, exc_value, exc_tb):
        if exc_type is None:
            self.log("Logging terminated normally")
            self.flush()
        else:
            if self.enable_exception_logging:
                self.log("Exception encountered; abnormal log termination.\n" \
                         f"Exception Type: {exc_type}\n" \
                         f"Exception Value: {exc_value}\n" \
                         f"{''.join(traceback.format_exception(None, exc_value, exc_value.__traceback__))}\n") # traceback
            else:
                self.log("Exception encountered; abnormal log termination.\nYou may want to enable exception logging if it is safe.\n")
            self.flush()
            return False # re-raise the exception
