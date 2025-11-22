"""
Locust load testing file for the Asgiri test ASGI application.

This file defines load testing scenarios for all endpoints exposed by the
test application in tests/app.py.

Usage:
    locust -f locustfile.py --host=http://localhost:8000
"""

import io
from locust import task, between, events
from locust.contrib.fasthttp import FastHttpUser


class AsgiriUser(FastHttpUser):
    """
    A user class for load testing the Asgiri test application.
    Uses FastHttpUser for better performance during load testing.
    """
    
    # Wait between 1 and 3 seconds between tasks
    wait_time = between(1, 3)
    
    @task(5)
    def get_root(self):
        """Test the root endpoint (weighted 5x)."""
        with self.client.get("/", catch_response=True) as response:
            if response.status_code == 200:
                resp_json = response.json()
                expected = "Welcome to the test ASGI application!"
                if resp_json.get("message") == expected:
                    response.success()
                else:
                    response.failure("Unexpected response content")
            else:
                response.failure(f"Got status code {response.status_code}")
    
    @task(5)
    def get_helloworld(self):
        """Test the /helloworld endpoint (weighted 5x)."""
        with self.client.get("/helloworld", catch_response=True) as response:
            if response.status_code == 200:
                resp_json = response.json()
                if resp_json.get("Hello") == "World":
                    response.success()
                else:
                    response.failure("Unexpected response content")
            else:
                response.failure(f"Got status code {response.status_code}")
    
    @task(3)
    def post_echo(self):
        """Test the /echo POST endpoint (weighted 3x)."""
        test_data = {
            "test": "data",
            "number": 42,
            "nested": {"key": "value"}
        }
        with self.client.post(
            "/echo",
            json=test_data,
            catch_response=True
        ) as response:
            if response.status_code == 200:
                resp_json = response.json()
                if resp_json == test_data:
                    response.success()
                else:
                    response.failure("Echo did not return the same data")
            else:
                response.failure(f"Got status code {response.status_code}")
    
    @task(2)
    def get_read_params(self):
        """
        Test the /read_params endpoint with query parameters (weighted 2x).
        """
        params = {
            "name": "TestUser",
            "age": "25",
            "active": "true"
        }
        with self.client.get(
            "/read_params",
            params=params,
            catch_response=True
        ) as response:
            if response.status_code == 200:
                resp_json = response.json()
                if (resp_json.get("Name") == "TestUser" and
                        resp_json.get("Age") == 25 and
                        resp_json.get("Active") is True):
                    response.success()
                else:
                    response.failure("Unexpected parameter parsing")
            else:
                response.failure(f"Got status code {response.status_code}")
    
    @task(1)
    def get_lifespan_records(self):
        """Test the /lifespan_records endpoint (weighted 1x)."""
        resp = self.client.get("/lifespan_records", catch_response=True)
        with resp as response:
            if response.status_code == 200:
                resp_json = response.json()
                if "records" in resp_json:
                    response.success()
                else:
                    response.failure("Missing 'records' key in response")
            else:
                response.failure(f"Got status code {response.status_code}")
    
    @task(1)
    def post_upload_formpost(self):
        """
        Test the /uploadfile/formpost endpoint with multipart form data.
        (weighted 1x)
        """
        # Create a small test file in memory
        test_content = b"This is test file content for load testing. " * 100
        files = {
            'file': ('test_file.txt', io.BytesIO(test_content),
                     'text/plain')
        }

        with self.client.post(
            "/uploadfile/formpost",
            files=files,
            catch_response=True
        ) as response:
            if response.status_code == 200:
                resp_json = response.json()
                if ("filename" in resp_json and
                        "content_size" in resp_json and
                        "sha256" in resp_json):
                    if resp_json["content_size"] == len(test_content):
                        response.success()
                    else:
                        expected = len(test_content)
                        actual = resp_json['content_size']
                        msg = f"Size mismatch: expected {expected}, "
                        msg += f"got {actual}"
                        response.failure(msg)
                else:
                    response.failure("Missing expected keys in response")
            else:
                response.failure(f"Got status code {response.status_code}")
    
    @task(1)
    def post_upload_rawpost(self):
        """
        Test the /uploadfile/rawpost endpoint with raw POST data.
        (weighted 1x)
        """
        # Create test content to upload
        test_content = b"Raw POST data for load testing. " * 100

        with self.client.post(
            "/uploadfile/rawpost",
            data=test_content,
            headers={"Content-Type": "application/octet-stream"},
            catch_response=True
        ) as response:
            if response.status_code == 200:
                resp_json = response.json()
                if "content_size" in resp_json and "sha256" in resp_json:
                    if resp_json["content_size"] == len(test_content):
                        response.success()
                    else:
                        expected = len(test_content)
                        actual = resp_json['content_size']
                        msg = f"Size mismatch: expected {expected}, "
                        msg += f"got {actual}"
                        response.failure(msg)
                else:
                    response.failure("Missing expected keys in response")
            else:
                response.failure(f"Got status code {response.status_code}")


class HeavyLoadUser(FastHttpUser):
    """
    A user class focused on heavier operations (file uploads).
    This can be used to test the server under heavy load scenarios.
    """
    
    wait_time = between(2, 5)
    
    @task
    def large_file_upload_formpost(self):
        """Upload a larger file via form post."""
        # Create a 1MB test file
        test_content = b"X" * (1024 * 1024)
        files = {
            'file': ('large_test.bin', io.BytesIO(test_content),
                     'application/octet-stream')
        }
        
        with self.client.post(
            "/uploadfile/formpost",
            files=files,
            catch_response=True,
            timeout=30
        ) as response:
            if response.status_code == 200:
                resp_json = response.json()
                if resp_json.get("content_size") == len(test_content):
                    response.success()
                else:
                    response.failure("Content size mismatch")
            else:
                response.failure(f"Got status code {response.status_code}")
    
    @task
    def large_file_upload_rawpost(self):
        """Upload a larger file via raw POST."""
        # Create a 1MB test file
        test_content = b"Y" * (1024 * 1024)
        
        with self.client.post(
            "/uploadfile/rawpost",
            data=test_content,
            headers={"Content-Type": "application/octet-stream"},
            catch_response=True,
            timeout=30
        ) as response:
            if response.status_code == 200:
                resp_json = response.json()
                if resp_json.get("content_size") == len(test_content):
                    response.success()
                else:
                    response.failure("Content size mismatch")
            else:
                response.failure(f"Got status code {response.status_code}")
