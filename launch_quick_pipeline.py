import os
import detect
detect.MODEL=os.environ.get('PERSON_MODEL',detect.MODEL)
import quick_pipeline
if __name__=='__main__':raise SystemExit(quick_pipeline.main())
